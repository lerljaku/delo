import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elohell import engine  # noqa: E402
from elohell.api import HIDDEN_NAME, App  # noqa: E402
from elohell.elo import DEFAULT_CONFIG, expected_score, rate_match  # noqa: E402
from elohell.parsers import ParseError, build_tournament, parse_text, player_id  # noqa: E402
from elohell.storage import LocalStorage  # noqa: E402

ADMIN = {"sub": "admin-1", "email": "admin@x", "cognito:groups": "[admin]"}
USER = {"sub": "user-1", "email": "alice@x"}

CSV = """round,player1,player2,player1_wins,player2_wins,draws
1,Alice,Bob,2,1,0
1,Carol,Dave,0,2,0
2,Alice,Dave,2,0,0
2,Bob,Carol,1,1,1
"""


def upload(app, content, name="T", date="2026-01-01", type="rel", **kw):
    return app.handle("POST", "/api/tournaments", kw, {"content": content, "name": name, "date": date, "type": type}, ADMIN)


class EloTests(unittest.TestCase):
    def test_expected_score(self):
        self.assertAlmostEqual(expected_score(1500, 1500), 0.5)
        self.assertAlmostEqual(expected_score(1700, 1500), 0.7597, places=4)

    def test_rate_match_equal_players(self):
        a, b = rate_match(DEFAULT_CONFIG, 1500, 1500, 1.0, 20, 20)
        self.assertAlmostEqual(a, 1516)
        self.assertAlmostEqual(b, 1484)

    def test_provisional_k(self):
        a, _ = rate_match(DEFAULT_CONFIG, 1500, 1500, 1.0, 0, 20)
        self.assertAlmostEqual(a, 1524)


class ParserTests(unittest.TestCase):
    def test_csv(self):
        t = build_tournament(content=CSV, name="FNM", date="2026-01-01", type="rel")
        self.assertEqual(t["sourceFormat"], "csv")
        self.assertEqual(len(t["players"]), 4)
        self.assertEqual(len(t["matches"]), 4)
        self.assertEqual(t["standings"][0]["playerId"], player_id("Alice"))

    def test_csv_with_bom_and_result_column(self):
        t = build_tournament(content="﻿Round,Player1,Player2,Result\n1,A,B,2-0\n", name="x", date="2026-01-01", type="casual")
        self.assertEqual(t["matches"][0]["p1Wins"], 2)

    def test_text_with_headers_and_bye(self):
        text = "Round 1\nAlice vs Bob 2-1\nCarol - BYE\nRound 2\nAlice vs. Carol 1-1-1\nBob bye\n"
        m = parse_text(text)
        self.assertEqual([x["round"] for x in m], [1, 1, 2, 2])
        self.assertIsNone(m[1]["p2"])
        self.assertEqual(m[2]["draws"], 1)

    def test_text_infers_rounds(self):
        m = parse_text("Alice vs Bob 2-0\nCarol vs Dave 2-1\nAlice vs Carol 2-0\n")
        self.assertEqual([x["round"] for x in m], [1, 1, 2])

    def test_json_meta(self):
        t = build_tournament(content='{"name":"J","date":"2026-02-02","type":"rel","matches":[{"round":1,"player1":"A","player2":"B","result":"2-1"}]}')
        self.assertEqual((t["name"], t["type"]), ("J", "rel"))

    def test_player_twice_in_round_rejected(self):
        with self.assertRaisesRegex(ParseError, "already plays"):
            build_tournament(content="Round 1\nA vs B 2-0\nA vs C 2-0", name="x", date="2026-01-01", type="rel")

    def test_bad_type(self):
        with self.assertRaisesRegex(ParseError, "Type"):
            build_tournament(content=CSV, name="x", date="2026-01-01", type="gp")

    def test_names_are_normalized(self):
        self.assertEqual(player_id("  alice   NOVAK "), player_id("Alice Novak"))
        self.assertEqual(player_id("Jiri Obraz"), player_id("Jiří Obraz"))
        self.assertEqual(player_id("Tomáš Schebelle ⚪🔵⚫"), player_id("Tomáš Schebelle 🔵"))
        t = build_tournament(content="💖 Jířa 💖 vs Bob 2-0", name="x", date="2026-01-01", type="rel")
        self.assertIn("Jířa", t["players"].values())


EVENTLINK = """1
Alice
1–0–0
21
Bob
0–1–0
2
Carol
0–1–0
02
Dave
1–0–0
Eve
1–0–0
20
Bye
Dave
2–0–0
20
Alice
1–1–0
2
Bob
1–1–0
11
Eve
1–0–1
Carol
0–1–0
20
Bye
7
"""


class EventlinkTests(unittest.TestCase):
    def test_blocks_byes_missing_table_numbers_and_trailing_table(self):
        t = build_tournament(content=EVENTLINK, name="x", date="2026-01-01", type="rel")
        self.assertEqual(t["sourceFormat"], "eventlink")
        rounds = [(m["round"], t["players"][m["p1"]], t["players"].get(m["p2"])) for m in t["matches"]]
        self.assertEqual(rounds, [
            (1, "Alice", "Bob"), (1, "Carol", "Dave"), (1, "Eve", None),
            (2, "Dave", "Alice"), (2, "Bob", "Eve"), (2, "Carol", None),
        ])
        draw = t["matches"][4]
        self.assertEqual((draw["p1Wins"], draw["p2Wins"]), (1, 1))

    def test_playoff_tables_reused(self):
        # bracket positions reuse table numbers; rounds come from players reappearing
        text = "2\nA\n5–1–0\n20\nB\n4–2–0\n2\nC\n5–1–0\n20\nD\n4–2–0\n1\nA\n6–1–0\n21\nC\n5–2–0\n"
        t = build_tournament(content=text, name="x", date="2026-01-01", type="rel")
        self.assertEqual([m["round"] for m in t["matches"]], [1, 1, 2])

    def test_second_event_rejected(self):
        second = "1\nZed\n1–0–0\n20\nYan\n0–1–0\n"
        with self.assertRaisesRegex(ParseError, "second event"):
            build_tournament(content=EVENTLINK.replace("7\n", "") + second, name="x", date="2026-01-01", type="rel")

    def test_garbage_reports_line(self):
        with self.assertRaisesRegex(ParseError, "line 1"):
            build_tournament(content="1\nAlice\n1–0–0\nBob\nfoo\n1–0–0\n2–0–0", name="x", date="2026-01-01", type="rel", fmt="eventlink")


class EngineTests(unittest.TestCase):
    def test_ladders_and_stats(self):
        rel = build_tournament(content=CSV, name="R", date="2026-01-01", type="rel")
        casual = build_tournament(content="Alice vs Bob 0-2\nAlice vs Bob 0-2", name="C", date="2026-01-02", type="casual")
        result = engine.compute_all([casual, rel])
        alice = player_id("Alice")
        rel_alice = result["ladders"]["rel"][alice]
        all_alice = result["ladders"]["all"][alice]
        self.assertEqual(rel_alice["stats"]["matches"], 2)
        self.assertEqual(all_alice["stats"]["matches"], 4)
        self.assertEqual(all_alice["stats"]["longestWinStreak"], 2)
        self.assertEqual(all_alice["stats"]["longestLossStreak"], 2)
        self.assertEqual(all_alice["stats"]["worstMatchup"]["playerId"], player_id("Bob"))
        self.assertEqual(all_alice["tournaments"][0]["name"], "C")  # newest first
        self.assertGreater(all_alice["stats"]["peakRating"], all_alice["rating"])
        self.assertEqual([h["seq"] for h in all_alice["history"]], [1, 2, 3, 4])

    def test_bye_not_rated(self):
        t = build_tournament(content="Round 1\nA vs B 2-0\nC - BYE", name="x", date="2026-01-01", type="rel")
        c = engine.compute_all([t])["ladders"]["rel"][player_id("C")]
        self.assertEqual(c["rating"], 1500)
        self.assertEqual(c["stats"]["matches"], 0)
        self.assertEqual(c["tournaments"][0]["byes"], 1)


class ApiTests(unittest.TestCase):
    """Runs against LocalStorage; test_aws_storage.py reruns it against AwsStorage."""

    def make_storage(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        return LocalStorage(self.tmp.name)

    def setUp(self):
        self.app = App(self.make_storage())

    def test_upload_requires_admin(self):
        status, _ = self.app.handle("POST", "/api/tournaments", {}, {"content": CSV}, USER)
        self.assertEqual(status, 403)
        status, _ = self.app.handle("POST", "/api/tournaments", {}, {"content": CSV}, {})
        self.assertEqual(status, 401)

    def test_upload_leaderboard_player_and_duplicate(self):
        status, body = upload(self.app, CSV, dryRun="1")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["newPlayers"]), 4)
        self.assertEqual(self.app.handle("GET", "/api/leaderboard", {}, {}, {})[1]["players"], [])

        status, body = upload(self.app, CSV)
        self.assertEqual(status, 200, body)
        self.assertEqual(upload(self.app, CSV)[0], 409)

        _, board = self.app.handle("GET", "/api/leaderboard", {"ladder": "rel"}, {}, {})
        self.assertEqual(board["players"][0]["displayName"], "Alice")
        _, all_board = self.app.handle("GET", "/api/leaderboard", {"ladder": "all"}, {}, {})
        self.assertEqual(len(all_board["players"]), 4)

        alice = player_id("Alice")
        status, p = self.app.handle("GET", f"/api/players/{alice}", {"ladder": "rel"}, {}, {})
        self.assertEqual(status, 200)
        self.assertEqual(p["rank"], 1)
        self.assertEqual(p["tournaments"][0]["matches"][0]["opponentName"], "Bob")
        _, h = self.app.handle("GET", f"/api/players/{alice}/history", {"ladder": "rel"}, {}, {})
        self.assertEqual(len(h["history"]), 2)
        # response mutation must not leak into storage
        _, again = self.app.handle("GET", f"/api/players/{alice}", {"ladder": "rel"}, {}, {})
        self.assertEqual(again["tournaments"][0]["matches"][0]["opponentName"], "Bob")

    def test_casual_only_player_404_on_rel(self):
        upload(self.app, "Zed vs Yan 2-0", type="casual")
        status, body = self.app.handle("GET", f"/api/players/{player_id('Zed')}", {"ladder": "rel"}, {}, {})
        self.assertEqual(status, 404)
        self.assertEqual(body["availableLadders"], ["all"])

    def test_delete_recalculates(self):
        _, body = upload(self.app, CSV)
        upload(self.app, "Alice vs Bob 0-2", date="2026-02-01")
        status, _ = self.app.handle("DELETE", f"/api/tournaments/{body['tournamentId']}", {}, {}, ADMIN)
        self.assertEqual(status, 200)
        _, board = self.app.handle("GET", "/api/leaderboard", {"ladder": "rel"}, {}, {})
        self.assertEqual({r["displayName"] for r in board["players"]}, {"Alice", "Bob"})

    def test_claim_approve_and_hide(self):
        upload(self.app, CSV)
        alice, bob = player_id("Alice"), player_id("Bob")
        self.assertEqual(self.app.handle("POST", "/api/me/privacy", {}, {"hidden": True}, USER)[0], 403)
        status, acc = self.app.handle("POST", "/api/me/claim", {}, {"playerId": alice}, USER)
        self.assertEqual((status, acc["claimStatus"]), (200, "pending"))
        _, claims = self.app.handle("GET", "/api/admin/claims", {}, {}, ADMIN)
        self.assertEqual(claims[0]["playerName"], "Alice")
        self.app.handle("POST", f"/api/admin/claims/{USER['sub']}", {}, {"approve": True}, ADMIN)
        status, _ = self.app.handle("POST", "/api/me/privacy", {}, {"hidden": True}, USER)
        self.assertEqual(status, 200)

        _, board = self.app.handle("GET", "/api/leaderboard", {}, {}, {})
        self.assertEqual(board["players"][0]["displayName"], HIDDEN_NAME)
        _, bob_page = self.app.handle("GET", f"/api/players/{bob}", {}, {}, {})
        self.assertEqual(bob_page["tournaments"][0]["matches"][0]["opponentName"], HIDDEN_NAME)
        _, t = self.app.handle("GET", f"/api/tournaments/{self.app.storage.list_tournaments()[0]['tournamentId']}", {}, {}, {})
        self.assertEqual(t["players"][alice], HIDDEN_NAME)

        # hidden flag survives recalculation
        self.app.handle("POST", "/api/admin/recalculate", {}, {}, ADMIN)
        _, board = self.app.handle("GET", "/api/leaderboard", {}, {}, {})
        self.assertEqual(board["players"][0]["displayName"], HIDDEN_NAME)

        # a second account cannot claim the same player
        other = {"sub": "user-2", "email": "fake@x"}
        self.assertEqual(self.app.handle("POST", "/api/me/claim", {}, {"playerId": alice}, other)[0], 409)

    def test_release_notes(self):
        note = {"version": "0.1.0", "title": "First", "date": "2026-09-30", "body": "## Hi"}
        self.assertEqual(self.app.handle("POST", "/api/release-notes", {}, note, ADMIN)[0], 200)
        _, notes = self.app.handle("GET", "/api/release-notes", {}, {}, {})
        self.assertEqual(notes[0]["version"], "0.1.0")
        _, full = self.app.handle("GET", "/api/release-notes/0.1.0", {}, {}, {})
        self.assertEqual(full["body"], "## Hi")

    def test_unknown_route(self):
        self.assertEqual(self.app.handle("GET", "/api/nope", {}, {}, {})[0], 404)
        self.assertEqual(self.app.handle("PUT", "/api/leaderboard", {}, {}, {})[0], 405)


class LambdaHandlerTests(unittest.TestCase):
    def setUp(self):
        from elohell import api

        self.api = api
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        api._app = App(LocalStorage(tmp.name))
        self.addCleanup(setattr, api, "_app", None)

    def event(self, method, path, body=None, query=None, claims=None):
        import base64
        import json

        ev = {"rawPath": path, "queryStringParameters": query,
              "requestContext": {"http": {"method": method}}}
        if body is not None:
            ev["body"] = base64.b64encode(json.dumps(body).encode()).decode()
            ev["isBase64Encoded"] = True
        if claims:
            ev["requestContext"]["authorizer"] = {"jwt": {"claims": claims}}
        return ev

    def test_upload_and_read_through_lambda_event(self):
        import json

        body = {"content": CSV, "name": "T", "date": "2026-01-01", "type": "rel"}
        resp = self.api.lambda_handler(self.event("POST", "/api/tournaments", body, claims=ADMIN), None)
        self.assertEqual(resp["statusCode"], 200, resp["body"])
        resp = self.api.lambda_handler(self.event("GET", "/api/leaderboard", query={"ladder": "rel"}), None)
        self.assertEqual(json.loads(resp["body"])["players"][0]["displayName"], "Alice")
        self.assertEqual(resp["headers"]["Content-Type"], "application/json")

    def test_invalid_json_body(self):
        ev = self.event("POST", "/api/tournaments", claims=ADMIN)
        ev["body"] = "{not json"
        self.assertEqual(self.api.lambda_handler(ev, None)["statusCode"], 400)


if __name__ == "__main__":
    unittest.main()
