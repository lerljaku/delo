"""Payments (Stripe webhook), membership perks and upcoming tournaments.
Runs against LocalStorage; AwsMembershipTests reruns everything against mocked AWS."""
import csv
import io
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from delo import payments  # noqa: E402
from delo.api import HIDDEN_NAME, App  # noqa: E402
from delo.parsers import player_id  # noqa: E402
from delo.storage import LocalStorage  # noqa: E402
from test_delo import ADMIN, CSV, USER, upload  # noqa: E402

SECRET = "whsec_test"
BOB = {"sub": "user-2", "email": "bob@x"}


class SignatureTests(unittest.TestCase):
    def test_verify(self):
        raw, now = '{"type": "x"}', 1_800_000_000
        header = payments.sign(raw, SECRET, now)
        payments.verify_signature(raw, header, SECRET, now=now + 10)
        for bad_raw, bad_header, at in ((raw + " ", header, now), (raw, header, now + 301),
                                        (raw, payments.sign(raw, "other", now), now), (raw, "garbage", now)):
            with self.assertRaises(payments.SignatureError):
                payments.verify_signature(bad_raw, bad_header, SECRET, now=at)


class MembershipTests(unittest.TestCase):
    def make_storage(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return LocalStorage(tmp.name)

    def setUp(self):
        self.app = App(self.make_storage(), stripe_secret=SECRET)
        _, self.t1 = upload(self.app, CSV, name="T1", date="2026-01-01")
        _, self.t2 = upload(self.app, "Round 1\nAlice vs Bob 0-2\nCarol - BYE", name="T2", date="2026-02-01")
        for claims, name in ((USER, "Alice"), (BOB, "Bob")):
            self.app.handle("POST", "/api/me/claim", {}, {"playerId": player_id(name)}, claims)
            self.app.handle("POST", f"/api/admin/claims/{claims['sub']}", {}, {"approve": True}, ADMIN)

    def call(self, method, path, claims=None, body=None, query=None):
        return self.app.handle(method, f"/api{path}", query or {}, body or {}, claims or {})

    def webhook(self, kind, obj, secret=SECRET):
        raw = json.dumps({"type": kind, "data": {"object": obj}})
        headers = {"Stripe-Signature": payments.sign(raw, secret, int(time.time()))}
        return self.app.handle("POST", "/api/stripe/webhook", {}, json.loads(raw), {}, raw=raw, headers=headers)

    def level(self, claims=USER):
        return self.call("GET", "/me", claims)[1]["effectiveMembership"]

    def grant(self, name, membership):
        self.call("POST", f"/admin/players/{player_id(name)}", ADMIN, {"membership": membership})

    # --- payments ------------------------------------------------------------
    def test_stripe_purchases_and_cancellation(self):
        self.assertEqual(self.level(), "")
        status, r = self.webhook("checkout.session.completed",
                                 {"mode": "payment", "client_reference_id": USER["sub"], "customer": "cus_1"})
        self.assertEqual((status, r["membership"]), (200, "supporter"))
        self.assertEqual(self.level(), "supporter")
        _, p = self.call("GET", f"/players/{player_id('Alice')}")
        self.assertEqual(p["membership"], "supporter")  # badge on the profile

        self.webhook("checkout.session.completed", {"mode": "subscription", "client_reference_id": USER["sub"],
                                                    "customer": "cus_1", "subscription": "sub_1"})
        self.assertEqual(self.level(), "diamond")
        self.webhook("customer.subscription.updated", {"customer": "cus_1", "status": "past_due"})
        self.assertEqual(self.level(), "diamond")  # Stripe is still retrying the card
        self.webhook("customer.subscription.deleted", {"customer": "cus_1", "status": "canceled"})
        self.assertEqual(self.level(), "supporter")  # the one-time Supporter purchase stays

        # bought before ever opening the account page: account is created, and the claim
        # approved later carries the membership to the profile
        self.webhook("checkout.session.completed", {"mode": "payment", "client_reference_id": "user-3",
                                                    "customer": "cus_3", "customer_details": {"email": "c@x"}})
        self.assertEqual(self.app.storage.get_account("user-3")["membership"], "supporter")

    def test_webhook_rejects_and_ignores(self):
        self.assertEqual(self.webhook("checkout.session.completed", {}, secret="wrong")[0], 400)
        self.assertEqual(self.webhook("invoice.paid", {})[1]["ignored"], "invoice.paid")
        self.assertEqual(self.webhook("customer.subscription.deleted", {"customer": "cus_x"})[1]["ignored"], "unknown customer")
        self.app.stripe_secret = ""
        self.assertEqual(self.webhook("checkout.session.completed", {})[0], 503)

    # --- perks ---------------------------------------------------------------
    def test_flair_and_profile_url(self):
        # flair: any signed-in user with an approved claim, no membership needed
        self.assertEqual(self.call("POST", "/me/profile", {"sub": "user-9"}, {"flair": "x"})[0], 403)  # no claimed profile
        status, prof = self.call("POST", "/me/profile", USER, {"flair": "Kroxa enjoyer", "flairColor": "purple"})
        self.assertEqual((status, prof["flair"], prof["flairColor"]), (200, "Kroxa enjoyer", "purple"))
        self.assertEqual(self.call("POST", "/me/profile", USER, {"flairColor": "pink"})[0], 400)
        self.grant("Alice", "supporter")
        self.assertEqual(self.call("POST", "/me/profile", USER, {"slug": "kroxa-king"})[0], 403)  # Diamond only

        self.grant("Alice", "diamond")
        self.assertEqual(self.call("POST", "/me/profile", USER, {"slug": "Bad Slug"})[0], 400)
        self.assertEqual(self.call("POST", "/me/profile", USER, {"slug": "kroxa-king"})[0], 200)
        _, found = self.call("GET", "/players/by-slug/kroxa-king")
        self.assertEqual(found["playerId"], player_id("Alice"))
        self.grant("Bob", "diamond")
        self.assertEqual(self.call("POST", "/me/profile", BOB, {"slug": "kroxa-king"})[0], 409)
        self.call("POST", "/me/profile", USER, {"slug": "alice"})  # renaming frees the old name
        self.assertEqual(self.call("POST", "/me/profile", BOB, {"slug": "kroxa-king"})[0], 200)
        _, p = self.call("GET", f"/players/{player_id('Alice')}")
        self.assertEqual((p["flair"], p["flairColor"], p["slug"]), ("Kroxa enjoyer", "purple", "alice"))
        board = {r["displayName"]: r for r in self.call("GET", "/leaderboard")[1]["players"]}
        self.assertEqual((board["Alice"]["flair"], board["Alice"]["flairColor"]), ("Kroxa enjoyer", "purple"))
        self.assertEqual(board["Bob"]["flair"], "")

        self.call("POST", "/me/privacy", USER, {"hidden": True})  # hidden: nothing that could identify
        _, p = self.call("GET", f"/players/{player_id('Alice')}")
        self.assertEqual((p["displayName"], p["flair"], p["slug"]), (HIDDEN_NAME, "", ""))
        self.assertEqual(self.call("GET", "/players/by-slug/alice")[0], 404)
        hidden = next(r for r in self.call("GET", "/leaderboard")[1]["players"] if r["playerId"] == player_id("Alice"))
        self.assertEqual((hidden["displayName"], hidden["flair"]), (HIDDEN_NAME, ""))


    def test_notes_archetypes_export(self):
        t1, t2 = self.t1["tournamentId"], self.t2["tournamentId"]
        self.assertEqual(self.call("POST", f"/me/notes/{t1}", USER, {"round": 1, "note": "x"})[0], 403)
        self.assertEqual(self.call("GET", "/me/archetypes", USER)[0], 403)
        self.assertEqual(self.call("GET", "/me/export", USER)[0], 403)
        self.grant("Alice", "diamond")
        self.assertEqual(self.call("POST", f"/me/notes/{t1}", USER, {"round": 9, "note": "x"})[0], 400)
        self.call("POST", f"/me/notes/{t1}", USER, {"round": 1, "note": "Mulled to 5 in game 3\nstill won"})
        self.assertEqual(self.call("GET", "/me/notes", USER)[1]["notes"], {t1: {"1": "Mulled to 5 in game 3\nstill won"}})

        self.call("POST", f"/me/decks/{t1}", USER, {"deck": "Kroxa"})
        self.call("POST", f"/me/decks/{t2}", USER, {"deck": "Kroxa"})
        self.call("POST", f"/me/decks/{t1}", BOB, {"deck": "Burn"})
        self.call("POST", f"/me/decks/{t2}", BOB, {"deck": "Burn"})
        _, stats = self.call("GET", "/me/archetypes", USER)
        # Alice: beat Bob (Burn) and Dave (no deck) in T1, lost to Bob in T2
        self.assertEqual([(r["deck"], r["wins"], r["losses"]) for r in stats["byOpponentDeck"]],
                         [("Burn", 1, 1), ("Unknown deck", 1, 0)])
        self.assertEqual([(r["deck"], r["matches"]) for r in stats["byOwnDeck"]], [("Kroxa", 3)])

        _, export = self.call("GET", "/me/export", USER)
        self.assertEqual(export["filename"], "delo-alice-history.csv")
        rows = list(csv.DictReader(io.StringIO(export["csv"])))
        self.assertEqual([(r["tournament"], r["round"], r["opponent"], r["result"]) for r in rows],
                         [("T1", "1", "Bob", "W"), ("T1", "2", "Dave", "W"), ("T2", "1", "Bob", "L")])
        self.assertEqual((rows[0]["my_deck"], rows[0]["opponent_deck"], rows[0]["note"]),
                         ("Kroxa", "Burn", "Mulled to 5 in game 3\nstill won"))
        self.assertNotEqual(rows[0]["elo_after"], "")

        self.grant("Alice", "")  # membership ended: notes stay readable and can be cleared
        self.assertEqual(len(self.call("GET", "/me/notes", USER)[1]["notes"][t1]), 1)
        self.assertEqual(self.call("POST", f"/me/notes/{t1}", USER, {"round": 1, "note": ""})[0], 200)

    # --- upcoming tournaments -----------------------------------------------
    def test_upcoming_tournaments(self):
        today = date.today()
        day = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
        event = {"name": "Modern Monday", "date": day(7), "time": "18:00", "location": "Najada Praha",
                 "format": "modern", "type": "rel", "link": "https://example.com/mm"}
        self.assertEqual(self.call("GET", "/upcoming"), (200, []))
        self.assertEqual(self.call("POST", "/upcoming", USER, event)[0], 403)
        self.assertFalse(self.call("GET", "/me/upcoming", USER)[1]["canPost"])

        self.grant("Alice", "diamond")
        status, posted = self.call("POST", "/upcoming", USER, event)
        self.assertEqual((status, posted["name"], posted["format"]), (200, "Modern Monday", "modern"))
        self.assertNotIn("createdBy", posted)
        self.call("POST", "/upcoming", ADMIN, {"name": "Legacy Cup", "date": day(2)})
        self.assertEqual([e["name"] for e in self.call("GET", "/upcoming")[1]], ["Legacy Cup", "Modern Monday"])
        self.assertEqual(self.call("GET", "/me/upcoming", USER)[1], {"canPost": True, "eventIds": [posted["eventId"]]})
        for bad in ({**event, "date": day(-1)}, {**event, "date": day(400)}, {**event, "name": ""},
                    {**event, "format": "pauper"}, {**event, "link": "nope"}):
            self.assertEqual(self.call("POST", "/upcoming", USER, bad)[0], 400, bad)

        self.assertEqual(self.call("DELETE", f"/upcoming/{posted['eventId']}", BOB)[0], 403)
        self.assertEqual(self.call("DELETE", f"/upcoming/{posted['eventId']}", USER)[0], 200)
        legacy = self.call("GET", "/upcoming")[1][0]["eventId"]
        self.assertEqual(self.call("DELETE", f"/upcoming/{legacy}", ADMIN)[0], 200)
        self.assertEqual(self.call("DELETE", f"/upcoming/{legacy}", ADMIN)[0], 404)

        for i in range(10):
            self.call("POST", "/upcoming", USER, {**event, "name": f"E{i}"})
        self.assertEqual(self.call("POST", "/upcoming", USER, event)[0], 400)  # per-member limit
        self.app.storage.put_upcoming({"eventId": "abc", "name": "Past", "date": day(-3), "createdBy": "x"})
        self.assertNotIn("Past", [e["name"] for e in self.call("GET", "/upcoming")[1]])


    def test_recurring_events_holidays_and_planned_list(self):
        today = date(2026, 12, 20)  # a Sunday
        patcher = mock.patch.object(App, "_today", staticmethod(lambda: today))
        patcher.start()
        self.addCleanup(patcher.stop)
        put = self.app.storage.put_upcoming
        put({"eventId": "a1", "name": "Modern Tuesday", "date": "2026-12-01", "recurrence": "weekly",
             "format": "modern", "createdBy": "x"})
        put({"eventId": "b2", "name": "Legacy Monthly", "date": "2026-10-24", "recurrence": "monthly", "createdBy": "x"})
        put({"eventId": "c3", "name": "Last week's cup", "date": "2026-12-10", "createdBy": "x"})
        put({"eventId": "d4", "name": "Old cup", "date": "2026-10-01", "createdBy": "x"})

        listed = self.call("GET", "/upcoming")[1]
        # recurring events: only the next date; one-off past events are gone
        self.assertEqual([(e["name"], e["date"]) for e in listed],
                         [("Modern Tuesday", "2026-12-22"), ("Legacy Monthly", "2026-12-24")])
        self.assertEqual((listed[0]["repeats"], listed[0]["holiday"]), ("Every Tuesday", None))
        self.assertEqual((listed[1]["repeats"], listed[1]["holiday"]), ("Monthly on the 24th", "Christmas Eve"))

        self.assertEqual(self.call("GET", "/me/planned-tournaments")[0], 401)  # anyone signed in can upload
        _, planned = self.call("GET", "/me/planned-tournaments", USER)
        self.assertEqual([(e["name"], e["date"]) for e in planned], [
            ("Legacy Monthly", "2026-11-24"), ("Modern Tuesday", "2026-12-01"), ("Modern Tuesday", "2026-12-08"),
            ("Last week's cup", "2026-12-10"), ("Modern Tuesday", "2026-12-15"), ("Modern Tuesday", "2026-12-22"),
            ("Legacy Monthly", "2026-12-24")])  # last 30 days + next of each, oldest first
        # the Chrome extension (no sign-in): the last 2 weeks, up to today
        put({"eventId": "e5", "name": "Sunday cube", "date": "2026-12-20", "createdBy": "x"})
        _, recent = self.call("GET", "/planned-tournaments", None, query={"days": "14"})
        self.assertEqual([(e["name"], e["date"]) for e in recent], [
            ("Modern Tuesday", "2026-12-08"), ("Last week's cup", "2026-12-10"), ("Modern Tuesday", "2026-12-15"),
            ("Sunday cube", "2026-12-20")])
        self.assertEqual(self.call("GET", "/planned-tournaments", None, query={"days": "x"})[0], 400)

        status, posted = self.call("POST", "/upcoming", ADMIN, {"name": "Pauper-free Friday", "date": "2026-12-25",
                                                                "recurrence": "weekly"})
        self.assertEqual((status, posted["date"], posted["holiday"], posted["recurrence"]), (200, "2026-12-25", "Christmas Day", "weekly"))
        self.assertEqual(self.call("POST", "/upcoming", ADMIN, {"name": "x", "date": "2026-12-25", "recurrence": "daily"})[0], 400)
        self.assertEqual(self.call("DELETE", f"/upcoming/{posted['eventId']}", ADMIN)[0], 200)

    def test_schedule_dates(self):
        from delo import schedule

        self.assertEqual(schedule.easter_sunday(2026), date(2026, 4, 5))
        self.assertEqual(schedule.holiday(date(2026, 4, 3)), "Good Friday")
        self.assertEqual(schedule.holiday(date(2026, 4, 6)), "Easter Monday")
        self.assertIsNone(schedule.holiday(date(2026, 4, 7)))
        self.assertEqual(schedule.occurrences(date(2026, 1, 31), "monthly", date(2026, 2, 1), date(2026, 4, 30)),
                         [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)])  # shorter months: last day
        tuesday = date(2026, 9, 29)
        self.assertEqual(schedule.next_occurrence(tuesday, "weekly", date(2026, 10, 6)), date(2026, 10, 6))  # today counts
        self.assertEqual(schedule.next_occurrence(tuesday, "weekly", date(2026, 10, 7)), date(2026, 10, 13))
        self.assertIsNone(schedule.next_occurrence(date(2026, 1, 1), None, date(2026, 1, 2)))


try:
    from moto import mock_aws
except ImportError:  # pragma: no cover
    mock_aws = None


@unittest.skipIf(mock_aws is None, "moto not installed")
class AwsMembershipTests(MembershipTests):
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
