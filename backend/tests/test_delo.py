import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from delo import engine  # noqa: E402
from delo.api import HIDDEN_NAME, App  # noqa: E402
from delo.elo import DEFAULT_CONFIG, EloConfig, expected_score, rate_match  # noqa: E402
from delo.parsers import (ParseError, build_tournament, parse_eventlink, parse_text, player_id,  # noqa: E402
                         split_eventlink, upload_content)
from delo.storage import LocalStorage  # noqa: E402

ADMIN = {"sub": "admin-1", "email": "admin@x", "cognito:groups": "[admin]"}
USER = {"sub": "user-1", "email": "alice@x"}

CSV = """round,player1,player2,player1_wins,player2_wins,draws
1,Alice,Bob,2,1,0
1,Carol,Dave,0,2,0
2,Alice,Dave,2,0,0
2,Bob,Carol,1,1,1
"""


def upload(app, content, name="T", date="2026-01-01", type="rel", format="modern", link="", **kw):
    body = {"content": content, "name": name, "date": date, "type": type, "format": format, "link": link}
    return app.handle("POST", "/api/tournaments", kw, body, ADMIN)


def strip_results(storage) -> dict:
    """Leaderboards, entries and history of every ladder, to compare incremental and full results."""
    from delo.elo import LADDER_IDS

    out = {}
    for lid in LADDER_IDS:
        board = storage.leaderboard(lid)
        out[lid] = {r["playerId"]: (r, storage.get_ladder_entry(r["playerId"], lid),
                                    storage.get_history(r["playerId"], lid)) for r in board}
    return out


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

    def test_game_format_and_link(self):
        t = build_tournament(content=CSV, name="x", date="2026-01-01", type="rel", game_format="Duel Commander",
                             link=" https://eventlink.wizards.com/events/123 ")
        self.assertEqual((t["format"], t["link"]), ("duel-commander", "https://eventlink.wizards.com/events/123"))
        bare = build_tournament(content=CSV, name="x", date="2026-01-01", type="rel")
        self.assertEqual((bare["format"], bare["link"]), (None, None))
        self.assertEqual(bare["id"], t["id"])  # format and link don't change identity
        with self.assertRaisesRegex(ParseError, "Format"):
            build_tournament(content=CSV, name="x", date="2026-01-01", type="rel", game_format="pauper")
        for bad in ("javascript:alert(1)", "eventlink.com/1", "https://a b.com"):
            with self.assertRaisesRegex(ParseError, "Link"):
                build_tournament(content=CSV, name="x", date="2026-01-01", type="rel", link=bad)

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

    def test_split_events(self):
        second = "1\nZed\n1–0–0\n20\nYan\n0–1–0\n"
        parts = split_eventlink(EVENTLINK.replace("7\n", "") + second)
        self.assertEqual(len(parts), 2)
        self.assertEqual(len(parse_eventlink(parts[0])), 6)
        self.assertEqual([m["p1"] for m in parse_eventlink(parts[1])], ["Zed"])

    def test_round_files_combined_in_round_order(self):
        cut = EVENTLINK.index("Dave\n2–0–0")
        files = [{"name": "r2.txt", "content": EVENTLINK[cut:]}, {"name": "r1.txt", "content": "﻿" + EVENTLINK[:cut]}]
        content, fmt = upload_content("", files)
        self.assertEqual(fmt, "eventlink")
        combined = build_tournament(content=content, name="x", date="2026-01-01", type="rel", fmt=fmt)
        whole = build_tournament(content=EVENTLINK, name="x", date="2026-01-01", type="rel")
        self.assertEqual(combined["matches"], whole["matches"])

        with self.assertRaisesRegex(ParseError, "r1b.txt and r1.txt both end with round 1"):
            upload_content("", files[1:] + [{"name": "r1b.txt", "content": EVENTLINK[:cut]}])
        with self.assertRaisesRegex(ParseError, "results.csv: several files can only be combined for Eventlink"):
            upload_content("", files + [{"name": "results.csv", "content": CSV}])
        with self.assertRaisesRegex(ParseError, "Eventlink"):
            upload_content("", files, "csv")
        with self.assertRaisesRegex(ParseError, "^r1.txt: line 1: expected a match block"):
            upload_content("", [files[0], {"name": "r1.txt", "content": "1\nA\n1–0–0\nfoo\nB\n0–1–0\n"}])
        self.assertEqual(upload_content("", [files[0]]), (files[0]["content"], "auto"))

    def test_scrub_raw(self):
        from delo.privacy import scrub_raw

        pid = player_id("Bob")
        self.assertEqual(scrub_raw(CSV, pid, ["Bob"], "csv"), CSV.replace("Bob", "Deleted player"))
        self.assertNotIn("Bob", scrub_raw(EVENTLINK.replace("Bob\n0–1–0", "BOB 🔵\n0–1–0"), pid, ["Bob"], "eventlink"))
        # "Bob" inside another player's name: can't be removed safely, caller falls back
        self.assertIsNone(scrub_raw(CSV.replace("Carol", "Bob Horvath"), pid, ["Bob"], "csv"))

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

    def test_format_ladders(self):
        modern = build_tournament(content="A vs B 2-0", name="M", date="2026-01-01", type="rel", game_format="modern")
        legacy = build_tournament(content="B vs A 2-0", name="L", date="2026-01-02", type="casual", game_format="legacy")
        none = build_tournament(content="A vs C 2-0", name="N", date="2026-01-03", type="rel")
        ladders = engine.compute_all([modern, legacy, none])["ladders"]
        a = player_id("A")
        self.assertEqual(ladders["rel"][a]["stats"]["matches"], 2)
        self.assertEqual(ladders["all"][a]["stats"]["matches"], 3)
        self.assertEqual(ladders["rel:modern"][a]["stats"]["matches"], 1)
        self.assertNotIn(a, ladders["rel:legacy"])  # casual only
        self.assertEqual(ladders["all:legacy"][a]["stats"]["wins"], 0)
        self.assertEqual(ladders["rel:vintage"], {})
        self.assertEqual(ladders["rel:modern"][a]["tournaments"][0]["format"], "modern")

    def test_apply_tournament_matches_full_replay(self):
        ts = [build_tournament(content=CSV, name="R", date="2026-01-01", type="rel", game_format="modern"),
              build_tournament(content="Alice vs Bob 0-2\nAlice vs Bob 1-1", name="C", date="2026-01-02", type="casual",
                               game_format="modern"),
              build_tournament(content="Bob vs Erin 2-1\nAlice vs Erin 2-0", name="R2", date="2026-01-03", type="rel",
                               game_format="legacy")]
        ts[1]["players"][player_id("Alice")] = "alice"  # three spellings used once each: most recent wins
        ts[2]["players"][player_id("Alice")] = "ALICE"
        stored = engine.compute_all(ts[:1])
        spellings = dict(stored["spellings"])
        for t in ts[1:]:
            step = engine.apply_tournament(t, {lid: {p: e for p, e in stored["ladders"].get(lid, {}).items()
                                                     if p in t["players"]} for lid in stored["ladders"]},
                                           {p: spellings[p] for p in t["players"] if p in spellings})
            spellings.update(step["spellings"])
            for lid, entries in step["ladders"].items():
                for pid, e in entries.items():
                    old = stored["ladders"][lid].get(pid, {"history": []})
                    stored["ladders"][lid][pid] = {**e, "history": old["history"] + e["history"]}
            stored["names"].update(step["names"])
        full = engine.compute_all(ts)
        self.assertEqual(stored["names"], full["names"])
        self.assertEqual(full["names"][player_id("Alice")], "ALICE")
        self.assertEqual(stored["ladders"], full["ladders"])


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

    def test_provisional_players(self):
        self.assertEqual(DEFAULT_CONFIG.min_matches, 1)
        app = App(self.app.storage, EloConfig(min_matches=10))
        # Alice and Bob play 10 matches; Carol 1 (plus a bye, which doesn't count), Dave 3
        rounds = [f"Round {r}\nAlice vs Bob {'2-0' if r % 3 else '0-2'}" for r in range(1, 11)]
        rounds[0] += "\nCarol - BYE"
        rounds[1] += "\nCarol vs Dave 0-2"
        rounds[2] += "\nDave vs Erin 2-1"
        rounds[3] += "\nDave vs Erin 2-1"
        upload(app, "\n".join(rounds))
        _, board = app.handle("GET", "/api/leaderboard", {}, {}, {})
        self.assertEqual(board["minMatches"], 10)
        rows = [(r["displayName"], r["rank"], r["provisional"], r["rating"] is None) for r in board["players"]]
        self.assertEqual(rows, [("Alice", 1, False, False), ("Bob", 2, False, False),
                                ("Dave", None, True, True), ("Erin", None, True, True), ("Carol", None, True, True)])
        self.assertIsNone(board["players"][2]["summary"]["peakRating"])
        _, dave = app.handle("GET", f"/api/players/{player_id('Dave')}", {}, {}, {})
        self.assertEqual((dave["rank"], dave["provisional"], dave["playerCount"]), (None, True, 2))
        _, alice = app.handle("GET", f"/api/players/{player_id('Alice')}", {}, {}, {})
        self.assertEqual((alice["rank"], alice["provisional"]), (1, False))
        _, t = app.handle("GET", f"/api/tournaments/{alice['tournaments'][0]['tournamentId']}", {}, {}, {})
        self.assertEqual(set(t["ratingChanges"]), {player_id("Alice"), player_id("Bob")})

    def test_incremental_upload_matches_full_recalculation(self):
        _, body = upload(self.app, CSV)
        self.assertEqual(body["recalculation"]["mode"], "incremental")
        upload(self.app, "Alice vs Bob 0-2\nCarol vs Erin 1-1-1", name="C", date="2026-01-02", type="casual")
        self.app.handle("POST", "/api/me/claim", {}, {"playerId": player_id("Bob")}, USER)
        self.app.handle("POST", f"/api/admin/claims/{USER['sub']}", {}, {"approve": True}, ADMIN)
        self.app.handle("POST", "/api/me/privacy", {}, {"hidden": True}, USER)
        _, body = upload(self.app, "bob vs Erin 2-1\nAlice vs Dave 2-0", name="L", date="2026-01-02", format="legacy")
        self.assertEqual(body["recalculation"]["mode"], "incremental")
        incremental = strip_results(self.app.storage)

        _, body = self.app.handle("POST", "/api/admin/recalculate", {}, {}, ADMIN)
        self.assertEqual(body["mode"], "full")
        self.assertEqual(strip_results(self.app.storage), incremental)
        _, h = self.app.handle("GET", f"/api/players/{player_id('Alice')}/history", {"ladder": "all"}, {}, {})
        self.assertEqual([x["seq"] for x in h["history"]], [1, 2, 3, 4])

    def test_backdated_upload_recalculates_everything(self):
        upload(self.app, CSV, date="2026-02-01")
        _, body = upload(self.app, "Alice vs Bob 0-2", date="2026-01-15")
        self.assertEqual(body["recalculation"]["mode"], "full")
        _, h = self.app.handle("GET", f"/api/players/{player_id('Alice')}/history", {}, {}, {})
        self.assertEqual([x["date"] for x in h["history"]], ["2026-01-15", "2026-02-01", "2026-02-01"])

    def test_format_leaderboards(self):
        upload(self.app, CSV, format="modern")
        upload(self.app, "Zed vs Yan 2-0", date="2026-01-02", format="Duel Commander")
        _, board = self.app.handle("GET", "/api/leaderboard", {"format": "duel-commander"}, {}, {})
        self.assertEqual((board["format"], [r["displayName"] for r in board["players"]]), ("duel-commander", ["Zed", "Yan"]))
        _, board = self.app.handle("GET", "/api/leaderboard", {}, {}, {})
        self.assertEqual(len(board["players"]), 6)
        _, board = self.app.handle("GET", "/api/leaderboard", {"format": "vintage"}, {}, {})
        self.assertEqual(board["players"], [])
        self.assertEqual(self.app.handle("GET", "/api/leaderboard", {"format": "pauper"}, {}, {})[0], 400)
        status, p = self.app.handle("GET", f"/api/players/{player_id('Zed')}", {"format": "duel-commander"}, {}, {})
        self.assertEqual((status, p["rank"], p["playerCount"]), (200, 1, 2))
        self.assertEqual(self.app.handle("GET", f"/api/players/{player_id('Zed')}", {"format": "modern"}, {}, {})[0], 404)

    def test_upload_several_files_and_rating_changes(self):
        cut = EVENTLINK.index("Dave\n2–0–0")
        body = {"files": [{"name": "r2.txt", "content": EVENTLINK[cut:]}, {"name": "r1.txt", "content": EVENTLINK[:cut]}],
                "name": "T", "date": "2026-01-01", "type": "casual", "format": "legacy"}
        status, r = self.app.handle("POST", "/api/tournaments", {}, body, ADMIN)
        self.assertEqual(status, 200, r)
        _, t = self.app.handle("GET", f"/api/tournaments/{r['tournamentId']}", {}, {}, {})
        self.assertEqual((t["sourceFormat"], len(t["matches"])), ("eventlink", 6))
        self.assertEqual(t["ratingLadder"], {"ladder": "all", "format": "legacy"})
        changes = t["ratingChanges"]
        self.assertEqual(set(changes), set(t["players"]))
        _, alice = self.app.handle("GET", f"/api/players/{player_id('Alice')}", {"ladder": "all", "format": "legacy"}, {}, {})
        self.assertEqual(changes[player_id("Alice")]["delta"], alice["tournaments"][0]["delta"])
        self.assertEqual(changes[player_id("Carol")]["ratingBefore"], 1500)
        self.assertEqual(self.app.handle("POST", "/api/tournaments", {}, {**body, "files": "x"}, ADMIN)[0], 400)

    def test_format_required_and_link(self):
        status, body = upload(self.app, CSV, format="")
        self.assertEqual(status, 400)
        self.assertIn("Format", body["error"])
        self.assertEqual(upload(self.app, CSV, link="not a link")[0], 400)
        link = "https://eventlink.wizards.com/events/1"
        _, body = upload(self.app, CSV, link=link)
        _, preview = upload(self.app, "A vs B 2-0", link=link, dryRun="1")
        self.assertEqual(preview["sameLink"][0]["tournamentId"], body["tournamentId"])
        _, rows = self.app.handle("GET", "/api/tournaments", {}, {}, {})
        self.assertEqual((rows[0]["format"], rows[0]["link"]), ("modern", link))

    def test_update_tournament_format_and_link(self):
        _, body = upload(self.app, CSV)
        tid = body["tournamentId"]
        path = f"/api/tournaments/{tid}"
        self.assertEqual(self.app.handle("POST", path, {}, {"format": "legacy"}, USER)[0], 403)
        self.assertEqual(self.app.handle("POST", path, {}, {"format": "pauper"}, ADMIN)[0], 400)
        self.assertEqual(self.app.handle("POST", "/api/tournaments/nope", {}, {"link": ""}, ADMIN)[0], 404)
        status, r = self.app.handle("POST", path, {}, {"format": "legacy"}, ADMIN)
        self.assertEqual((status, r["tournament"]["format"], r["recalculation"]["mode"]), (200, "legacy", "full"))
        self.assertEqual(self.app.handle("GET", "/api/leaderboard", {"format": "modern"}, {}, {})[1]["players"], [])
        self.assertEqual(len(self.app.handle("GET", "/api/leaderboard", {"format": "legacy"}, {}, {})[1]["players"]), 4)
        _, r = self.app.handle("POST", path, {}, {"link": "https://example.com/t"}, ADMIN)
        self.assertIsNone(r["recalculation"])
        self.assertEqual(self.app.handle("GET", path, {}, {}, {})[1]["link"], "https://example.com/t")

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

    def test_erase_player(self):
        _, first = upload(self.app, CSV)
        upload(self.app, "Round 1\nAlice vs Bob 0-2\nCarol - BYE", name="Second", date="2026-02-01")
        alice = player_id("Alice")
        self.app.handle("POST", "/api/me/claim", {}, {"playerId": alice}, USER)
        before = {r["displayName"]: r["rating"] for r in self.app.handle("GET", "/api/leaderboard", {}, {}, {})[1]["players"]}
        self.assertEqual(self.app.handle("POST", f"/api/admin/players/{alice}/erase", {}, {}, USER)[0], 403)

        status, r = self.app.handle("POST", f"/api/admin/players/{alice}/erase", {}, {}, ADMIN)
        self.assertEqual((status, r["tournaments"]), (200, 2), r)
        after = {r["displayName"]: r["rating"] for r in self.app.handle("GET", "/api/leaderboard", {}, {}, {})[1]["players"]}
        self.assertEqual(after.pop("Deleted player"), before.pop("Alice"))  # same matches, anonymous
        self.assertEqual(after, before)  # nobody else's rating changed
        for meta in self.app.storage.list_tournaments():
            raw, _ = self.app.storage.get_raw(meta["tournamentId"])
            self.assertNotIn("alice", raw.lower())
            t = self.app.handle("GET", f"/api/tournaments/{meta['tournamentId']}", {}, {}, {})[1]
            self.assertNotIn(alice, t["players"])
            self.assertIn("Deleted player", t["players"].values())
        self.assertEqual(self.app.handle("GET", f"/api/players/{alice}", {}, {}, {})[0], 404)
        self.assertEqual(self.app.handle("GET", "/api/me", {}, {}, USER)[1]["claimStatus"], "none")

        # stays anonymous in later uploads; re-uploading the original is still a duplicate
        _, again = upload(self.app, "Alice vs Erin 2-0", name="Third", date="2026-03-01")
        self.assertEqual((again["erasedPlayers"], again["newPlayers"]), (1, ["Erin"]))
        raw, _ = self.app.storage.get_raw(again["tournamentId"])
        self.assertNotIn("Alice", raw)
        self.assertEqual(upload(self.app, CSV)[0], 409)
        self.assertEqual(self.app.handle("POST", f"/api/admin/players/{alice}/erase", {}, {}, ADMIN)[0], 404)

    def test_decks(self):
        _, t1 = upload(self.app, CSV, name="T1", date="2026-01-01")
        _, t2 = upload(self.app, "Alice vs Bob 2-0", name="T2", date="2026-02-01")
        _, leg = upload(self.app, "Alice vs Carol 2-0", name="L", date="2026-03-01", format="legacy")
        t1, t2, leg = t1["tournamentId"], t2["tournamentId"], leg["tournamentId"]
        bob_user = {"sub": "user-2", "email": "bob@x"}
        self.assertEqual(self.app.handle("GET", "/api/me/decks", {}, {}, USER)[0], 403)  # no approved claim
        for claims, name in ((USER, "Alice"), (bob_user, "Bob")):
            self.app.handle("POST", "/api/me/claim", {}, {"playerId": player_id(name)}, claims)
            self.app.handle("POST", f"/api/admin/claims/{claims['sub']}", {}, {"approve": True}, ADMIN)
        deck = lambda claims, tid, name: self.app.handle("POST", f"/api/me/decks/{tid}", {}, {"deck": name}, claims)  # noqa: E731

        deck(USER, t1, "Murktide")
        deck(USER, t2, "  Kroxa ")
        deck(USER, leg, "Delver")
        deck(bob_user, t1, "Amulet Titan")
        deck(bob_user, t2, "Burn")
        _, mine = self.app.handle("GET", "/api/me/decks", {}, {}, USER)
        self.assertEqual([(r["name"], r["deck"]) for r in mine["tournaments"]], [("L", "Delver"), ("T2", "Kroxa"), ("T1", "Murktide")])
        # own decks newest first (last played Kroxa), then other players' decks in the format
        self.assertEqual(mine["suggestions"]["modern"], ["Kroxa", "Murktide", "Amulet Titan", "Burn"])
        self.assertEqual(mine["suggestions"]["legacy"], ["Delver"])

        self.assertEqual(deck(bob_user, t1, "kroxa")[1]["deck"], "Kroxa")  # existing spelling reused
        self.assertEqual(deck(USER, "nope", "X")[0], 404)
        self.assertEqual(deck(bob_user, leg, "X")[0], 404)  # Bob didn't play it
        self.assertEqual(deck(USER, t1, "x" * 61)[0], 400)
        self.assertEqual(deck(USER, t1, "")[1]["deck"], None)

        _, detail = self.app.handle("GET", f"/api/tournaments/{t2}", {}, {}, {})
        self.assertEqual(detail["decks"], {player_id("Alice"): "Kroxa", player_id("Bob"): "Burn"})
        _, alice = self.app.handle("GET", f"/api/players/{player_id('Alice')}", {"ladder": "all"}, {}, {})
        self.assertEqual([t["deck"] for t in alice["tournaments"]], ["Delver", "Kroxa", None])
        self.assertNotIn("decks", self.app.handle("GET", "/api/tournaments", {}, {}, {})[1][0])

        # decks survive a format change, and go away when the player is erased
        self.app.handle("POST", f"/api/tournaments/{t2}", {}, {"format": "premodern"}, ADMIN)
        self.assertEqual(self.app.handle("GET", f"/api/tournaments/{t2}", {}, {}, {})[1]["decks"][player_id("Bob")], "Burn")
        self.app.handle("POST", f"/api/admin/players/{player_id('Bob')}/erase", {}, {}, ADMIN)
        self.assertEqual(self.app.handle("GET", f"/api/tournaments/{t2}", {}, {}, {})[1]["decks"], {player_id("Alice"): "Kroxa"})

    def test_delete_account(self):
        upload(self.app, CSV)
        alice = player_id("Alice")
        self.app.handle("POST", "/api/me/claim", {}, {"playerId": alice}, USER)
        self.app.handle("POST", f"/api/admin/claims/{USER['sub']}", {}, {"approve": True}, ADMIN)
        self.assertEqual(self.app.handle("DELETE", "/api/me", {}, {}, {})[0], 401)
        status, _ = self.app.handle("DELETE", "/api/me", {}, {}, USER)
        self.assertEqual(status, 200)
        self.assertIsNone(self.app.storage.get_account(USER["sub"]))
        self.assertEqual(self.app.storage.get_profiles([alice])[alice]["ownerSub"], "")
        # the profile can be claimed again, e.g. by the same person with a new account
        other = {"sub": "user-2", "email": "alice2@x"}
        self.assertEqual(self.app.handle("POST", "/api/me/claim", {}, {"playerId": alice}, other)[0], 200)

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
        from delo import api

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

        body = {"content": CSV, "name": "T", "date": "2026-01-01", "type": "rel", "format": "modern"}
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
