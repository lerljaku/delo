"""Islands: separately rated groups of players, and the migration of existing data into one.
Runs against LocalStorage, then mocked AWS."""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from delo.api import HIDDEN_NAME, App  # noqa: E402
from delo.elo import EloConfig  # noqa: E402
from delo.parsers import player_id  # noqa: E402
from delo.storage import LocalStorage  # noqa: E402
from migrate_islands import migrate  # noqa: E402
from test_delo import ADMIN, CSV, USER, upload  # noqa: E402

CZ, FR = "czech-island", "france-island"


def board(app, island=None, **query):
    q = {**query, **({"island": island} if island else {})}
    return {r["displayName"]: r for r in app.handle("GET", "/api/leaderboard", q, {}, {})[1]["players"]}


class IslandTests(unittest.TestCase):
    def make_storage(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return LocalStorage(tmp.name)

    def setUp(self):
        self.app = App(self.make_storage(), EloConfig(min_matches=1))
        # existing data from before islands: one tournament, one upcoming event
        upload(self.app, CSV, name="Old FNM")
        self.app.storage.put_upcoming({"eventId": "e1", "name": "Weekly", "date": (date.today() + timedelta(days=3)).isoformat(),
                                       "recurrence": "weekly", "createdBy": "x"})
        self.before = board(self.app)

    def run_migration(self, **kw):
        with redirect_stdout(io.StringIO()):
            return migrate(self.app.storage, **kw)

    def test_migration_moves_everything_into_czech_island(self):
        self.assertEqual(self.run_migration(dry_run=True)["tournaments"], 1)
        self.assertEqual(self.app.storage.list_islands(), [])  # dry run changed nothing
        r = self.run_migration()
        self.assertEqual((r["island"], r["tournaments"], r["upcoming"]), (CZ, 1, 1))
        self.assertEqual(self.app.handle("GET", "/api/islands", {}, {}, {})[1], [{"islandId": CZ, "name": "Czech Island", "icon": "🇨🇿"}])
        self.assertEqual([m["island"] for m in self.app.storage.list_tournaments()], [CZ])
        self.assertEqual(self.app.storage.leaderboard("rel"), [])  # the old ladders are gone
        after = board(self.app)  # default island
        self.assertEqual({n: r["rating"] for n, r in after.items()}, {n: r["rating"] for n, r in self.before.items()})
        self.assertEqual(board(self.app, CZ).keys(), after.keys())
        self.assertEqual([e["name"] for e in self.app.handle("GET", "/api/upcoming", {}, {}, {})[1]], ["Weekly"])
        self.assertEqual(self.run_migration()["tournaments"], 0)  # running it again changes nothing

    def test_islands_are_rated_separately(self):
        self.run_migration()
        self.assertEqual(self.app.handle("POST", "/api/admin/islands", {}, {"name": "France Island"}, USER)[0], 403)
        self.assertEqual(self.app.handle("POST", "/api/admin/islands", {}, {"name": "France Island", "icon": "FR"}, ADMIN)[0], 400)
        status, fr = self.app.handle("POST", "/api/admin/islands", {}, {"name": "France Island", "icon": "🇫🇷"}, ADMIN)
        self.assertEqual((status, fr["islandId"], fr["icon"]), (200, FR, "🇫🇷"))
        self.assertEqual(self.app.handle("POST", "/api/admin/islands", {}, {"name": "france island"}, ADMIN)[0], 409)
        # Alice also plays in France: a separate rating there, unaffected by her Czech results
        _, r = upload(self.app, "Pierre vs Alice 2-0\nJean vs Marie 2-1", name="Paris Open", date="2026-02-01", island=FR)
        cz, france = board(self.app, CZ), board(self.app, FR)
        self.assertNotIn("Pierre", cz)
        self.assertEqual(set(france), {"Pierre", "Alice", "Jean", "Marie"})
        self.assertLess(france["Alice"]["rating"], 1500)  # lost her only French match
        self.assertGreater(cz["Alice"]["rating"], 1500)   # won both Czech matches
        self.assertEqual(cz["Alice"]["rating"], self.before["Alice"]["rating"])
        _, p = self.app.handle("GET", f"/api/players/{player_id('Pierre')}", {"island": CZ}, {}, {})
        self.assertEqual(p["error"], "Player has no rated matches in this ladder")
        self.assertEqual([t["name"] for t in self.app.handle("GET", "/api/tournaments", {"island": FR}, {}, {})[1]], ["Paris Open"])
        self.assertEqual([t["name"] for t in self.app.handle("GET", "/api/tournaments", {"island": CZ}, {}, {})[1]], ["Old FNM"])
        self.assertEqual(board(self.app, "atlantis").keys(), cz.keys())  # unknown island: the default

        # moving a tournament between islands recalculates both
        status, moved = self.app.handle("POST", f"/api/tournaments/{r['tournamentId']}", {}, {"island": CZ}, ADMIN)
        self.assertEqual((status, moved["recalculation"]["mode"]), (200, "full"))
        self.assertIn("Pierre", board(self.app, CZ))
        self.assertEqual(board(self.app, FR), {})
        self.assertEqual(self.app.handle("POST", f"/api/tournaments/{r['tournamentId']}", {}, {"island": "nope"}, ADMIN)[0], 400)

        _, renamed = self.app.handle("POST", f"/api/admin/islands/{FR}", {}, {"name": "France"}, ADMIN)
        self.assertEqual((renamed["name"], renamed["icon"]), ("France", "🇫🇷"))  # icon kept
        _, changed = self.app.handle("POST", f"/api/admin/islands/{FR}", {}, {"icon": "🥖"}, ADMIN)
        self.assertEqual((changed["name"], changed["icon"]), ("France", "🥖"))  # name kept
        _, admin_list = self.app.handle("GET", "/api/admin/islands", {}, {}, ADMIN)
        self.assertEqual([(i["islandId"], i["tournaments"]) for i in admin_list], [(CZ, 2), (FR, 0)])

    def test_upcoming_and_hidden_players_per_island(self):
        self.run_migration()
        self.app.handle("POST", "/api/admin/islands", {}, {"name": "France Island"}, ADMIN)
        day = (date.today() + timedelta(days=5)).isoformat()
        self.app.handle("POST", "/api/upcoming", {"island": FR}, {"name": "Paris Weekly", "date": day}, ADMIN)
        self.assertEqual([e["name"] for e in self.app.handle("GET", "/api/upcoming", {"island": FR}, {}, {})[1]], ["Paris Weekly"])
        self.assertEqual([e["name"] for e in self.app.handle("GET", "/api/upcoming", {"island": CZ}, {}, {})[1]], ["Weekly"])
        # hiding a player reaches their entries in every island's ladders
        upload(self.app, "Alice vs Pierre 2-0", name="Paris Cup", date="2026-03-01", island=FR)
        self.app.handle("POST", f"/api/admin/players/{player_id('Alice')}", {}, {"hidden": True}, ADMIN)
        self.assertIn(HIDDEN_NAME, board(self.app, CZ))
        self.assertIn(HIDDEN_NAME, board(self.app, FR))


try:
    from moto import mock_aws
except ImportError:  # pragma: no cover
    mock_aws = None


@unittest.skipIf(mock_aws is None, "moto not installed")
class AwsIslandTests(IslandTests):
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
