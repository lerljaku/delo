"""HTTP API. `App.handle` is transport-agnostic; `lambda_handler` adapts API Gateway
HTTP API (payload v2) events, and backend/local_server.py adapts http.server.
"""
from __future__ import annotations

import base64
import json
import os
import re
import traceback
from datetime import date as date_cls, datetime, timezone

from . import engine, privacy
from .elo import DEFAULT_CONFIG, GAME_FORMATS, LADDERS, ladder_id, ladders_for
from .parsers import ParseError, build_tournament, parse_game_format, parse_link, upload_content

HIDDEN_NAME = "Hidden player"
MEMBERSHIPS = ("", "supporter", "diamond")


class HttpError(Exception):
    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ladder(query: dict) -> tuple[str, str]:
    """(ladder, format) from ?ladder=rel|all&format=<game format>; no format = all formats."""
    ladder = query.get("ladder") or "rel"
    if ladder not in LADDERS:
        raise HttpError(400, f"ladder must be one of {', '.join(LADDERS)}")
    game_format = query.get("format") or ""
    if game_format and game_format not in GAME_FORMATS:
        raise HttpError(400, f"format must be one of {', '.join(GAME_FORMATS)}")
    return ladder, game_format


def _groups(claims: dict) -> set[str]:
    raw = claims.get("cognito:groups") or []
    if isinstance(raw, str):
        raw = raw.strip("[]").replace(",", " ").split()
    return set(raw)


class App:
    def __init__(self, storage, cfg=DEFAULT_CONFIG):
        self.storage = storage
        self.cfg = cfg
        r = self._routes = []
        add = lambda m, p, f, auth=None: r.append((m, re.compile(f"^/api{p}$"), f, auth))  # noqa: E731
        add("GET", "/leaderboard", self.leaderboard)
        add("GET", r"/players/(?P<pid>[0-9a-f]{12})", self.player)
        add("GET", r"/players/(?P<pid>[0-9a-f]{12})/history", self.player_history)
        add("GET", "/tournaments", self.tournaments)
        add("GET", r"/tournaments/(?P<tid>[\w-]+)", self.tournament)
        add("GET", "/release-notes", self.release_notes)
        add("GET", r"/release-notes/(?P<version>[\w.-]+)", self.release_note)
        add("GET", "/elo-model", lambda **_: self.cfg.to_dict())
        add("POST", "/tournaments", self.upload, "admin")
        add("POST", r"/tournaments/(?P<tid>[\w-]+)", self.update_tournament, "admin")
        add("DELETE", r"/tournaments/(?P<tid>[\w-]+)", self.delete_tournament, "admin")
        add("POST", "/admin/recalculate", lambda **_: self.recalculate(), "admin")
        add("POST", "/release-notes", self.publish_release_note, "admin")
        add("GET", "/admin/claims", self.list_claims, "admin")
        add("POST", r"/admin/claims/(?P<sub>[\w-]+)", self.review_claim, "admin")
        add("POST", r"/admin/players/(?P<pid>[0-9a-f]{12})", self.admin_update_player, "admin")
        add("POST", r"/admin/players/(?P<pid>[0-9a-f]{12})/erase", self.erase_player, "admin")
        add("GET", "/me", self.me, "user")
        add("DELETE", "/me", self.delete_me, "user")
        add("POST", "/me/claim", self.claim, "user")
        add("POST", "/me/privacy", self.privacy, "user")
        add("GET", "/me/decks", self.my_decks, "user")
        add("POST", r"/me/decks/(?P<tid>[\w-]+)", self.set_my_deck, "user")

    # --- dispatch ----------------------------------------------------------
    def handle(self, method: str, path: str, query: dict, body: dict, claims: dict) -> tuple[int, dict | list]:
        try:
            for m, pattern, fn, auth in self._routes:
                match = pattern.match(path)
                if not match or m != method:
                    continue
                if auth and not claims.get("sub"):
                    raise HttpError(401, "Sign in required")
                if auth == "admin" and "admin" not in _groups(claims):
                    raise HttpError(403, "Admin only")
                return 200, fn(query=query, body=body, claims=claims, **match.groupdict())
            if any(p.match(path) for _, p, _, _ in self._routes):
                raise HttpError(405, "Method not allowed")
            raise HttpError(404, "Not found")
        except HttpError as e:
            return e.status, {"error": e.message, **e.extra}
        except ParseError as e:
            return 400, {"error": str(e)}

    # --- helpers -----------------------------------------------------------
    def _names(self, pids) -> dict[str, str]:
        pids = {p for p in pids if p}
        profiles = self.storage.get_profiles(pids)
        return {pid: (HIDDEN_NAME if p.get("hidden") else p.get("displayName", "?")) for pid, p in profiles.items()}

    def recalculate(self) -> dict:
        tournaments = self.storage.load_all_tournaments()
        result = engine.compute_all(tournaments, self.cfg)
        self.storage.write_results(result)
        return {"mode": "full", "tournaments": len(tournaments), "players": len(result["names"]),
                "modelVersion": self.cfg.to_dict()["modelVersion"]}

    def rate_new_tournament(self, t: dict, earlier: list[dict]) -> dict:
        """Rate a just-saved tournament on top of the stored ratings. Falls back to a full
        recalculation when that would give a different result: when it is dated before an
        already rated tournament, or the stored ratings predate incremental updates or the
        current Elo model."""
        if any(engine.sort_key(m) > engine.sort_key(t) for m in earlier):
            return self.recalculate()
        prior = self.storage.get_ladder_entries(ladders_for(t), t["players"])
        # every rated player has an "all" entry; other profiles belong to deleted tournaments only
        profiles = self.storage.get_profiles(prior["all"])
        stored = [e for entries in prior.values() for e in entries.values()]
        if not all(engine.can_resume(e) for e in stored) or any("spellings" not in p for p in profiles.values()):
            return self.recalculate()
        spellings = {pid: p["spellings"] for pid, p in profiles.items()}
        result = engine.apply_tournament(t, prior, spellings, self.cfg)
        self.storage.write_results(result, incremental=True)
        return {"mode": "incremental", "tournaments": 1, "players": len(result["names"]),
                "modelVersion": self.cfg.to_dict()["modelVersion"]}

    # --- public ------------------------------------------------------------
    def _ranked(self, lid: str) -> list[dict]:
        """Leaderboard rows: ranked players (>= min_matches rated matches) by rating, then
        provisional ones (rank None) by matches played, so their unshown ratings don't show
        through the order."""
        rows = self.storage.leaderboard(lid)
        for row in rows:
            row["provisional"] = row["summary"]["matches"] < self.cfg.min_matches
            if row["hidden"]:
                row["displayName"] = HIDDEN_NAME
        ranked = [r for r in rows if not r["provisional"]]
        for i, row in enumerate(ranked, start=1):
            row["rank"] = i
        provisional = sorted((r for r in rows if r["provisional"]),
                             key=lambda r: (-r["summary"]["matches"], r["displayName"].casefold()))
        for row in provisional:
            row["rank"] = row["rating"] = row["summary"]["peakRating"] = None
        return ranked + provisional

    def leaderboard(self, query, **_):
        ladder, game_format = _ladder(query)
        return {"ladder": ladder, "format": game_format, "minMatches": self.cfg.min_matches,
                "players": self._ranked(ladder_id(ladder, game_format))}

    def player(self, pid, query, **_):
        ladder, game_format = _ladder(query)
        entry = self.storage.get_ladder_entry(pid, ladder_id(ladder, game_format))
        if not entry:
            others = [l for l in LADDERS if l != ladder and self.storage.get_ladder_entry(pid, ladder_id(l, game_format))]
            raise HttpError(404, "Player has no rated matches in this ladder", availableLadders=others)
        stats = entry["stats"]
        opp_ids = {m["opponentId"] for t in entry["tournaments"] for m in t["matches"]}
        opp_ids |= {mu["playerId"] for mu in (stats.get("bestMatchup"), stats.get("worstMatchup")) if mu}
        names = self._names(opp_ids)
        for mu in (stats.get("bestMatchup"), stats.get("worstMatchup")):
            if mu:
                mu["displayName"] = names.get(mu["playerId"], "?")
        for t in entry["tournaments"]:
            for m in t["matches"]:
                m["opponentName"] = names.get(m["opponentId"], "BYE") if m["opponentId"] else "BYE"
        board = [r for r in self._ranked(ladder_id(ladder, game_format)) if not r["provisional"]]
        rank = next((r["rank"] for r in board if r["playerId"] == pid), None)
        return {
            "playerId": pid, "ladder": ladder, "format": game_format, "rank": rank, "playerCount": len(board),
            "provisional": stats["matches"] < self.cfg.min_matches, "minMatches": self.cfg.min_matches,
            "displayName": HIDDEN_NAME if entry["hidden"] else entry["displayName"],
            "hidden": entry["hidden"], "membership": entry.get("membership", ""),
            "rating": entry["rating"], "stats": stats, "tournaments": self._with_decks(pid, entry["tournaments"]),
        }

    def _with_decks(self, pid: str, rows: list[dict]) -> list[dict]:
        decks = {m["tournamentId"]: (m.get("decks") or {}).get(pid) for m in self.storage.list_tournaments()}
        return [{**r, "deck": decks.get(r["tournamentId"])} for r in rows]

    def player_history(self, pid, query, **_):
        ladder, game_format = _ladder(query)
        history = self.storage.get_history(pid, ladder_id(ladder, game_format))
        names = self._names(h["opponentId"] for h in history)
        for h in history:
            h["opponentName"] = names.get(h["opponentId"], "?")
        return {"playerId": pid, "ladder": ladder, "format": game_format, "initialRating": self.cfg.initial_rating,
                "history": history}

    def tournaments(self, **_):
        return [{k: v for k, v in m.items() if k not in ("s3Key", "rawKey", "decks")} for m in self.storage.list_tournaments()]

    def tournament(self, tid, **_):
        t = self.storage.get_tournament(tid)
        if not t:
            raise HttpError(404, "Tournament not found")
        names = self._names(t["players"])
        t["players"] = {pid: names.get(pid, name) for pid, name in t["players"].items()}
        # Elo change of each player in this tournament, in the ladder its player links open:
        # REL or REL + Casual by type, limited to its format when it has one
        ladder = "rel" if t["type"] == "rel" else "all"
        lid = ladder_id(ladder, t.get("format"))
        t["ratingLadder"] = {"ladder": ladder, "format": t.get("format") or ""}
        meta = next((m for m in self.storage.list_tournaments() if m["tournamentId"] == tid), {})
        t["decks"] = meta.get("decks") or {}
        t["ratingChanges"] = {
            pid: {k: row[k] for k in ("ratingBefore", "ratingAfter", "delta")}
            for pid, entry in self.storage.get_ladder_entries([lid], t["players"])[lid].items()
            if entry["stats"]["matches"] >= self.cfg.min_matches  # provisional players show no Elo
            for row in entry["tournaments"] if row["tournamentId"] == tid
        }
        return t

    def release_notes(self, **_):
        return [{k: v for k, v in r.items() if k != "s3Key"} for r in self.storage.list_release_notes()]

    def release_note(self, version, **_):
        note = self.storage.get_release_note(version)
        if not note:
            raise HttpError(404, "Release note not found")
        note.pop("s3Key", None)
        return note

    # --- admin -------------------------------------------------------------
    def upload(self, query, body, claims, **_):
        """Body: content, name, date, type (rel|casual), format (game format, required),
        link (optional public page of the event), sourceFormat (file format, default auto).
        Instead of content: files, [{name, content}], e.g. one Eventlink export per round."""
        content, source_format = upload_content(body.get("content", ""), body.get("files"),
                                                body.get("sourceFormat", "auto"))
        t = build_tournament(
            content=content, name=body.get("name", ""), date=body.get("date", ""),
            type=body.get("type", ""), fmt=source_format,
            game_format=body.get("format", ""), link=body.get("link", ""),
            uploaded_by=claims.get("email", claims["sub"]), uploaded_at=_now(),
        )
        if not t["format"]:
            raise HttpError(400, f"Format is required: one of {', '.join(GAME_FORMATS.values())}")
        ext = {"csv": "csv", "json": "json"}.get(t["sourceFormat"], "txt")
        # players who had their data erased stay anonymous in new uploads too
        erased = self.storage.erased_ids(t["players"])
        for pid in erased:
            spelling = t["players"][pid]
            t = privacy.anonymize(t, pid, privacy.new_anonymous_id())
            content = content and privacy.scrub_raw(content, pid, [spelling], t["sourceFormat"])
        if not content:  # a name could not be removed from the raw text: store the normalized form only
            content, ext = json.dumps(t), "json"
        known = self.storage.get_profiles(t["players"])
        new_players = sorted(name for pid, name in t["players"].items()
                             if pid not in known and name != privacy.ERASED_NAME)
        stored = self.storage.list_tournaments()
        duplicate = any(m["tournamentId"] == t["id"] for m in stored)
        same_link = [{"tournamentId": m["tournamentId"], "name": m["name"], "date": m["date"]}
                     for m in stored if t["link"] and m.get("link") == t["link"]]
        if query.get("dryRun") in ("1", "true"):
            return {"dryRun": True, "tournament": t, "newPlayers": new_players, "duplicate": duplicate,
                    "sameLink": same_link, "erasedPlayers": len(erased)}
        if duplicate:
            raise HttpError(409, "This exact tournament has already been uploaded", tournamentId=t["id"])
        self.storage.save_tournament(t, content, ext)
        return {"tournamentId": t["id"], "newPlayers": new_players, "sameLink": same_link,
                "erasedPlayers": len(erased), "recalculation": self.rate_new_tournament(t, stored)}

    def update_tournament(self, tid, body, **_):
        """Change the game format and/or link of an uploaded tournament."""
        fields = {}
        if "format" in body:
            fields["format"] = parse_game_format(body["format"])
        if "link" in body:
            fields["link"] = parse_link(body["link"])
        before = self.storage.get_tournament(tid)
        if not before:
            raise HttpError(404, "Tournament not found")
        t = self.storage.update_tournament(tid, **fields)
        recalculation = self.recalculate() if t.get("format") != before.get("format") else None
        return {"tournament": t, "recalculation": recalculation}

    def delete_tournament(self, tid, **_):
        if not self.storage.delete_tournament(tid):
            raise HttpError(404, "Tournament not found")
        return {"deleted": tid, "recalculation": self.recalculate()}

    def publish_release_note(self, body, **_):
        version = str(body.get("version", "")).strip()
        if not re.fullmatch(r"[\w.-]+", version):
            raise HttpError(400, "version is required (letters, digits, . _ -)")
        date = body.get("date") or date_cls.today().isoformat()
        if not body.get("title") or not body.get("body"):
            raise HttpError(400, "title and body are required")
        meta = {"version": version, "title": body["title"], "date": date, "summary": body.get("summary", "")}
        self.storage.put_release_note(meta, body["body"])
        return meta

    def list_claims(self, query, **_):
        accounts = self.storage.list_accounts(query.get("status") or "pending")
        names = self.storage.get_profiles(a["playerId"] for a in accounts if a.get("playerId"))
        for a in accounts:
            a["playerName"] = names.get(a.get("playerId"), {}).get("displayName")
        return accounts

    def review_claim(self, sub, body, **_):
        account = self.storage.get_account(sub)
        if not account or account.get("claimStatus") != "pending":
            raise HttpError(404, "No pending claim for this account")
        if body.get("approve"):
            pid = account["playerId"]
            prof = self.storage.get_profiles([pid]).get(pid)
            if prof and prof.get("ownerSub") and prof["ownerSub"] != sub:
                raise HttpError(409, "Player is already claimed by another account")
            self.storage.update_profile(pid, ownerSub=sub, membership=account.get("membership", ""))
            account["claimStatus"] = "approved"
        else:
            account["claimStatus"] = "rejected"
        account["claimReviewedAt"] = _now()
        self.storage.put_account(account)
        return account

    def admin_update_player(self, pid, body, **_):
        fields = {}
        if "hidden" in body:
            fields["hidden"] = bool(body["hidden"])
        if "membership" in body:
            if body["membership"] not in MEMBERSHIPS:
                raise HttpError(400, f"membership must be one of {MEMBERSHIPS}")
            fields["membership"] = body["membership"]
        prof = self.storage.update_profile(pid, **fields)
        if not prof:
            raise HttpError(404, "Player not found")
        return prof

    def erase_player(self, pid, **_):
        """GDPR erasure: the player becomes an anonymous "Deleted player" in every stored
        tournament (raw uploads included) and in future uploads. Their matches stay, under a new
        random id, so other players' ratings don't change."""
        tournaments = [t for t in self.storage.load_all_tournaments() if pid in t["players"]]
        profile = self.storage.get_profiles([pid]).get(pid)
        if not tournaments and not profile:
            raise HttpError(404, "Player not found")
        spellings = set((profile or {}).get("spellings", {})) | {t["players"][pid] for t in tournaments}
        new_id = privacy.new_anonymous_id()
        for t in tournaments:
            raw, ext = self.storage.get_raw(t["id"])
            anonymous = privacy.anonymize(t, pid, new_id)
            scrubbed = privacy.scrub_raw(raw, pid, spellings, t["sourceFormat"])
            self.storage.replace_tournament(anonymous, scrubbed or json.dumps(anonymous),
                                            ext if scrubbed is not None else "json")
            self.storage.set_deck(t["id"], pid, None)
        for account in self.storage.list_accounts():
            if account.get("playerId") == pid:
                account.update(playerId=None, claimStatus="none", claimNote="")
                self.storage.put_account(account)
        self.storage.delete_player(pid)
        self.storage.add_erased(pid)
        return {"erased": pid, "tournaments": len(tournaments),
                "recalculation": self.recalculate() if tournaments else None}

    # --- signed-in user ----------------------------------------------------
    def _account(self, claims) -> dict:
        account = self.storage.get_account(claims["sub"])
        if not account:
            account = {"sub": claims["sub"], "email": claims.get("email", ""), "playerId": None,
                       "claimStatus": "none", "membership": "", "createdAt": _now()}
            self.storage.put_account(account)
        return account

    def me(self, claims, **_):
        account = self._account(claims)
        player = None
        if account.get("playerId"):
            player = self.storage.get_profiles([account["playerId"]]).get(account["playerId"])
        return {**account, "isAdmin": "admin" in _groups(claims), "player": player}

    def delete_me(self, claims, **_):
        """Delete the signed-in user's account and login. A claimed player profile stays (it comes
        from public tournament results) but is no longer linked to anyone; erasing the results
        themselves is an admin action (erase_player)."""
        account = self.storage.get_account(claims["sub"])
        if account and account.get("claimStatus") == "approved":
            prof = self.storage.get_profiles([account["playerId"]]).get(account["playerId"])
            if prof and prof.get("ownerSub") == claims["sub"]:
                self.storage.update_profile(account["playerId"], ownerSub="", membership="")
        self.storage.delete_account(claims["sub"])
        self.storage.delete_login(claims.get("cognito:username") or claims["sub"])
        return {"deleted": True}

    def claim(self, claims, body, **_):
        account = self._account(claims)
        if account["claimStatus"] == "approved":
            raise HttpError(409, "You already have an approved player profile")
        pid = body.get("playerId", "")
        prof = self.storage.get_profiles([pid]).get(pid)
        if not prof:
            raise HttpError(404, "Player not found")
        if prof.get("ownerSub"):
            raise HttpError(409, "This player has already been claimed")
        account.update(playerId=pid, claimStatus="pending", claimRequestedAt=_now(),
                       claimNote=str(body.get("note", ""))[:500])
        self.storage.put_account(account)
        return account

    def _owned_player(self, claims) -> str:
        account = self._account(claims)
        if account["claimStatus"] != "approved":
            raise HttpError(403, "Your player profile claim must be approved first")
        return account["playerId"]

    def my_decks(self, claims, **_):
        """The signed-in player's tournaments (newest first) with their decks, and deck name
        suggestions per format: the player's own decks, most recently played first, then the
        other decks entered for that format, alphabetically."""
        pid = self._owned_player(claims)
        entry = self.storage.get_ladder_entry(pid, "all") or {"tournaments": []}  # "all" has every tournament
        metas = {m["tournamentId"]: m for m in self.storage.list_tournaments()}
        rows = [{k: t.get(k) for k in ("tournamentId", "name", "date", "format", "type")}
                | {"deck": metas.get(t["tournamentId"], {}).get("decks", {}).get(pid)}
                for t in entry["tournaments"]]
        return {"playerId": pid, "tournaments": rows, "suggestions": _deck_suggestions(metas.values(), pid)}

    def set_my_deck(self, claims, tid, body, **_):
        pid = self._owned_player(claims)
        t = self.storage.get_tournament(tid)
        if not t or pid not in t["players"]:
            raise HttpError(404, "You didn't play in this tournament")
        deck = " ".join(str(body.get("deck") or "").split())
        if len(deck) > 60 or any(not c.isprintable() for c in deck):
            raise HttpError(400, "Deck name must be at most 60 printable characters")
        # reuse an existing spelling ("kroxa" -> "Kroxa") so the same deck isn't listed twice
        known = {d.casefold(): d for m in self.storage.list_tournaments() if m.get("format") == t.get("format")
                 for d in (m.get("decks") or {}).values()}
        deck = known.get(deck.casefold(), deck) or None
        self.storage.set_deck(tid, pid, deck)
        return {"tournamentId": tid, "deck": deck}

    def privacy(self, claims, body, **_):
        account = self._account(claims)
        if account["claimStatus"] != "approved":
            raise HttpError(403, "Your player profile claim must be approved first")
        return self.storage.update_profile(account["playerId"], hidden=bool(body.get("hidden")))


def _deck_suggestions(metas, pid: str) -> dict[str, list[str]]:
    """{format or "": deck names} for the deck picker; see App.my_decks."""
    own: dict[str, list[str]] = {}
    others: dict[str, set[str]] = {}
    for m in sorted(metas, key=engine.sort_key, reverse=True):
        fmt = m.get("format") or ""
        for player, deck in (m.get("decks") or {}).items():
            if player == pid:
                if deck not in own.setdefault(fmt, []):
                    own[fmt].append(deck)
            else:
                others.setdefault(fmt, set()).add(deck)
    return {fmt: own.get(fmt, []) + sorted(others.get(fmt, set()) - set(own.get(fmt, [])), key=str.casefold)
            for fmt in own.keys() | others.keys()}


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------
_app: App | None = None


def _get_app() -> App:
    global _app
    if _app is None:
        if os.environ.get("STORAGE", "aws") == "aws":
            from .storage import AwsStorage
            _app = App(AwsStorage())
        else:
            from .storage import LocalStorage
            _app = App(LocalStorage(os.environ.get("LOCAL_DATA_DIR", ".localdata")))
    return _app


def response(status: int, payload) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(payload),
    }


def lambda_handler(event, context):
    try:
        http = event["requestContext"]["http"]
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8")
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return response(400, {"error": "Body must be JSON"})
        claims = (event["requestContext"].get("authorizer") or {}).get("jwt", {}).get("claims", {})
        status, payload = _get_app().handle(http["method"], event["rawPath"],
                                            event.get("queryStringParameters") or {}, body, claims)
        return response(status, payload)
    except Exception:  # noqa: BLE001 - last-resort guard so clients always get JSON
        traceback.print_exc()
        return response(500, {"error": "Internal server error"})
