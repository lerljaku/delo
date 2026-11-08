"""backend/reparse_eventlink.py: stored Eventlink tournaments read again with the current parser."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from delo.api import App  # noqa: E402
from delo.parsers import build_tournament, player_id  # noqa: E402
from delo.storage import LocalStorage  # noqa: E402
from reparse_eventlink import reparse  # noqa: E402

# A and B draw intentionally (030); C beats D
PASTE = "1\nA\n0–0–1\n030\nB\n0–0–1\n2\nC\n1–0–0\n20\nD\n0–1–0\n"
LINK = "https://eventlink.wizards.com/stores/1/events/42"


class ReparseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.storage = LocalStorage(tmp.name)
        # stored the way the old parser read it: 030 as a 0-3 loss for A
        t = build_tournament(content=PASTE, name="Cup", date="2026-01-01", type="rel", fmt="eventlink",
                             game_format="legacy", link=LINK, uploaded_by="alice@x", uploaded_at="2026-01-02T10:00:00Z")
        t["matches"][0].update(p1Wins=0, p2Wins=3, draws=0)
        t["id"], t["island"] = "2026-01-01-cup-old000", None
        self.storage.save_tournament(t, PASTE, "txt")
        self.storage.set_player_value(t["id"], "decks", player_id("A"), "Lands")
        self.storage.claim_eventlink("42", t["id"], None)
        App(self.storage).recalculate()

    def run_quietly(self, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return reparse(self.storage, **kw)

    def test_replaces_misread_tournaments_once(self):
        self.assertEqual(len(self.run_quietly(dry_run=True)["replaced"]), 1)
        self.assertEqual([m["tournamentId"] for m in self.storage.list_tournaments()], ["2026-01-01-cup-old000"])

        result = self.run_quietly()
        [(old, new)] = result["replaced"]
        stored = self.storage.list_tournaments()
        self.assertEqual([m["tournamentId"] for m in stored], [new])
        t = self.storage.get_tournament(new)
        self.assertEqual((t["matches"][0]["p1Wins"], t["matches"][0]["draws"], t["matches"][0]["p2Wins"]), (0, 3, 0))
        self.assertEqual((t["name"], t["format"], t["link"], t["uploadedBy"], t["uploadedAt"]),
                         ("Cup", "legacy", LINK, "alice@x", "2026-01-02T10:00:00Z"))
        self.assertEqual(stored[0]["decks"], {player_id("A"): "Lands"})
        self.assertEqual(self.storage.get_eventlink("42"), new)  # the extension's next upload finds it
        a = self.storage.get_ladder_entry(player_id("A"), "rel")
        self.assertEqual((a["rating"], a["stats"]["draws"], a["stats"]["losses"]), (1500, 1, 0))  # the draw isn't rated

        self.assertEqual(self.run_quietly()["replaced"], [])  # running it again changes nothing


if __name__ == "__main__":
    unittest.main()
