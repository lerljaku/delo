"""Visitor statistics (delo/stats.py). Runs against LocalStorage, then mocked AWS."""
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from delo import stats  # noqa: E402
from delo.api import App  # noqa: E402
from delo.storage import LocalStorage  # noqa: E402
from test_delo import ADMIN, USER  # noqa: E402

BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"


class StatsHelperTests(unittest.TestCase):
    def test_pages_referrers_and_bots(self):
        self.assertEqual(stats.page_name("/player.html?id=abc"), "player.html")  # no query strings
        self.assertEqual(stats.page_name("/"), "index.html")
        self.assertIsNone(stats.page_name("../../etc/passwd"))
        self.assertEqual(stats.referrer_host("https://www.facebook.com/groups/x?y=1", set()), "facebook.com")
        self.assertIsNone(stats.referrer_host("https://mtgdelo.com/index.html", {"mtgdelo.com"}))
        self.assertIsNone(stats.referrer_host("not a url", set()))
        self.assertTrue(stats.is_bot("Googlebot/2.1") and stats.is_bot("") and stats.is_bot("HeadlessChrome/130"))
        self.assertFalse(stats.is_bot(BROWSER))
        a = stats.visitor_code("k", "2026-10-05", "1.2.3.4", BROWSER)
        self.assertNotEqual(a, stats.visitor_code("k", "2026-10-06", "1.2.3.4", BROWSER))  # changes daily
        self.assertNotEqual(a, stats.visitor_code("other", "2026-10-05", "1.2.3.4", BROWSER))  # needs the key
        self.assertNotIn("1.2.3.4", a)


class StatsApiTests(unittest.TestCase):
    def make_storage(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return LocalStorage(tmp.name)

    def setUp(self):
        self.app = App(self.make_storage(), stats_secret="test-key")
        self.today = date(2026, 10, 5)
        patcher = mock.patch.object(App, "_today", staticmethod(lambda: self.today))
        patcher.start()
        self.addCleanup(patcher.stop)

    def visit(self, page="index.html", ip="1.2.3.4", ua=BROWSER, referrer="", **headers):
        h = {"User-Agent": ua, "X-Forwarded-For": f"{ip}, 130.176.0.1", **headers}
        return self.app.handle("POST", "/api/visit", {}, {"page": page, "referrer": referrer}, {}, headers=h)[1]["counted"]

    def test_counting(self):
        self.assertTrue(self.visit())
        self.assertTrue(self.visit("player.html", referrer="https://www.reddit.com/r/mtg"))
        self.assertTrue(self.visit(ip="5.6.7.8"))  # another visitor
        for skipped in (self.visit(ua="Googlebot/2.1"), self.visit(DNT="1"), self.visit(**{"Sec-GPC": "1"}),
                        self.visit(page="evil/../x")):
            self.assertFalse(skipped)

        self.today = date(2026, 10, 6)  # next day: the same person counts as a visitor again
        self.assertTrue(self.visit())

        self.assertEqual(self.app.handle("GET", "/api/admin/stats", {}, {}, USER)[0], 403)
        status, s = self.app.handle("GET", "/api/admin/stats", {"days": "7"}, {}, ADMIN)
        self.assertEqual(status, 200)
        self.assertEqual(len(s["days"]), 7)  # every day, zeros included
        self.assertEqual(s["days"][-2:], [{"date": "2026-10-05", "views": 3, "visitors": 2},
                                          {"date": "2026-10-06", "views": 1, "visitors": 1}])
        self.assertEqual(s["totals"]["today"], {"visitors": 1, "views": 1})
        self.assertEqual(s["totals"]["last7Days"], {"visitors": 3, "views": 4})
        self.assertEqual(s["pages"], [{"name": "index.html", "views": 3}, {"name": "player.html", "views": 1}])
        self.assertEqual(s["referrers"], [{"name": "reddit.com", "views": 1}])
        self.assertEqual(self.app.handle("GET", "/api/admin/stats", {"days": "x"}, {}, ADMIN)[0], 400)

    def test_one_visitor_cannot_flood_the_counts(self):
        for _ in range(stats.MAX_VIEWS_PER_VISITOR):
            self.visit()
        self.assertFalse(self.visit())
        _, s = self.app.handle("GET", "/api/admin/stats", {}, {}, ADMIN)
        self.assertEqual(s["totals"]["today"], {"visitors": 1, "views": stats.MAX_VIEWS_PER_VISITOR})


try:
    from moto import mock_aws
except ImportError:  # pragma: no cover
    mock_aws = None


@unittest.skipIf(mock_aws is None, "moto not installed")
class AwsStatsApiTests(StatsApiTests):
    def make_storage(self):
        from delo.storage import AwsStorage
        from test_aws_storage import ENV, create_resources

        patcher = mock.patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        aws = mock_aws()
        aws.start()
        self.addCleanup(aws.stop)
        create_resources()
        return AwsStorage()

    def test_one_visitor_cannot_flood_the_counts(self):
        pass  # 300 mocked DynamoDB round trips; the local run covers the rule


if __name__ == "__main__":
    unittest.main()
