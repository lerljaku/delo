"""Merging a misspelled player into the right one. Runs against LocalStorage, then mocked AWS."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from delo.api import App  # noqa: E402
from delo.elo import EloConfig  # noqa: E402
from delo.parsers import player_id  # noqa: E402
from delo.storage import LocalStorage  # noqa: E402
from test_delo import ADMIN, CSV, USER, upload  # noqa: E402

ALICE, TYPO, BOB = player_id("Alice"), player_id("Alcie"), player_id("Bob")


class MergeTests(unittest.TestCase):
    def make_storage(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return LocalStorage(tmp.name)

    def setUp(self):
        self.app = App(self.make_storage(), EloConfig(min_matches=1))
        upload(self.app, CSV, name="FNM 1", date="2026-01-01")  # Alice, Bob, Carol, Dave
        _, self.t2 = upload(self.app, "Alcie vs Bob 2-0\nCarol vs Dave 2-1", name="FNM 2", date="2026-02-01")  # typo

    def merge(self, pid=TYPO, into=ALICE, claims=ADMIN, **query):
        return self.app.handle("POST", f"/api/admin/players/{pid}/merge", query, {"into": into}, claims)

    def names(self):
        return {r["displayName"]: r for r in self.app.handle("GET", "/api/leaderboard", {"ladder": "all"}, {}, {})[1]["players"]}

    def test_merge(self):
        self.app.handle("POST", "/api/me/claim", {}, {"playerId": ALICE}, USER)
        self.app.handle("POST", f"/api/admin/claims/{USER['sub']}", {}, {"approve": True}, ADMIN)
        self.app.handle("POST", f"/api/me/decks/{self.t2['tournamentId']}", {}, {"deck": "x"}, USER)  # 404: Alcie isn't hers yet
        self.app.handle("POST", f"/api/admin/players/{TYPO}", {}, {"membership": "supporter"}, ADMIN)  # 💎 given to the typo
        self.assertEqual(self.merge(claims=USER)[0], 403)
        self.assertEqual(self.merge(into=TYPO)[0], 400)
        self.assertEqual(self.merge(into="0123456789ab")[0], 404)

        _, preview = self.merge(dryRun="1")
        self.assertEqual((preview["from"], preview["into"], [t["name"] for t in preview["tournaments"]], preview["conflicts"]),
                         ("Alcie", "Alice", ["FNM 2"], []))
        self.assertIn("Alcie", self.names())  # nothing changed yet

        status, r = self.merge()
        self.assertEqual((status, r["merged"], r["recalculation"]["mode"]), (200, True, "full"), r)
        board = self.names()
        self.assertNotIn("Alcie", board)
        self.assertEqual(board["Alice"]["summary"]["matches"], 3)  # 2 from FNM 1 + the one as "Alcie"
        self.assertEqual(board["Alice"]["membership"], "supporter")  # carried over
        _, t2 = self.app.handle("GET", f"/api/tournaments/{self.t2['tournamentId']}", {}, {}, {})
        self.assertEqual(t2["players"][ALICE], "Alice")
        self.assertNotIn(TYPO, t2["players"])
        self.assertEqual(self.app.handle("GET", f"/api/players/{TYPO}", {"ladder": "all"}, {}, {})[0], 404)
        # her account now owns the merged results
        self.assertEqual(self.app.handle("POST", f"/api/me/decks/{self.t2['tournamentId']}", {}, {"deck": "Kroxa"}, USER)[0], 200)

        # the same typo in a later upload goes straight to Alice
        _, preview = upload(self.app, "Alcie vs Carol 2-0", name="FNM 3", date="2026-03-01", dryRun="1")
        self.assertEqual((preview["mergedNames"], preview["newPlayers"]), (["Alcie → Alice"], []))
        upload(self.app, "Alcie vs Carol 2-0", name="FNM 3", date="2026-03-01")
        self.assertEqual(self.names()["Alice"]["summary"]["matches"], 4)
        # re-uploading the original file is still recognised as a duplicate
        self.assertEqual(upload(self.app, "Alcie vs Bob 2-0\nCarol vs Dave 2-1", name="FNM 2", date="2026-02-01")[0], 409)

    def test_decks_move_and_merging_the_other_way(self):
        player_two = {"sub": "user-2", "email": "two@x"}
        self.app.handle("POST", "/api/me/claim", {}, {"playerId": TYPO}, player_two)
        self.app.handle("POST", f"/api/admin/claims/{player_two['sub']}", {}, {"approve": True}, ADMIN)
        self.app.handle("POST", f"/api/me/decks/{self.t2['tournamentId']}", {}, {"deck": "Burn"}, player_two)
        status, r = self.merge()  # Alcie is claimed: merge the other way
        self.assertEqual(status, 409)
        self.assertIn("Merge the other way", r["error"])

        self.assertEqual(self.merge(pid=ALICE, into=TYPO)[0], 200)  # Alice → Alcie (the claimed one)
        _, t2 = self.app.handle("GET", f"/api/tournaments/{self.t2['tournamentId']}", {}, {}, {})
        self.assertEqual(t2["decks"], {TYPO: "Burn"})  # her deck stayed with her
        upload(self.app, "Alice vs Carol 2-0", name="FNM 4", date="2026-04-01")
        self.assertNotIn("Alice", self.names())  # "Alice" now goes to Alcie

    def test_chained_merges(self):
        upload(self.app, "Ann vs Pat 2-0", name="A", date="2026-03-01")
        upload(self.app, "Anne vs Kim 2-0", name="B", date="2026-03-02")
        upload(self.app, "Annie vs Lou 2-0", name="C", date="2026-03-03")
        self.assertEqual(self.merge(pid=player_id("Ann"), into=player_id("Anne"))[0], 200)
        self.assertEqual(self.merge(pid=player_id("Anne"), into=player_id("Annie"))[0], 200)
        self.assertEqual(self.names()["Annie"]["summary"]["matches"], 3)
        _, preview = upload(self.app, "Ann vs Max 2-0", name="D", date="2026-03-04", dryRun="1")
        self.assertEqual(preview["mergedNames"], ["Ann → Annie"])  # the first merge follows the second

    def test_misspelling_of_an_erased_player_stays_anonymous(self):
        self.merge()  # Alcie → Alice
        self.app.handle("POST", f"/api/admin/players/{ALICE}/erase", {}, {}, ADMIN)
        _, preview = upload(self.app, "Alcie vs Carol 2-0", name="FNM 5", date="2026-05-01", dryRun="1")
        self.assertEqual(preview["erasedPlayers"], 1)
        self.assertNotIn("Alcie", preview["tournament"]["players"].values())

    def test_two_people_in_one_tournament_are_not_merged(self):
        status, r = self.merge(pid=player_id("Carol"), into=player_id("Dave"))
        self.assertEqual(status, 409)
        self.assertEqual([t["name"] for t in r["conflicts"]], ["FNM 1", "FNM 2"])
        _, preview = self.merge(pid=player_id("Carol"), into=player_id("Dave"), dryRun="1")
        self.assertEqual(len(preview["conflicts"]), 2)


try:
    from moto import mock_aws
except ImportError:  # pragma: no cover
    mock_aws = None


@unittest.skipIf(mock_aws is None, "moto not installed")
class AwsMergeTests(MergeTests):
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


if __name__ == "__main__":
    unittest.main()
