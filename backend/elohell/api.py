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

from . import engine
from .elo import DEFAULT_CONFIG, LADDERS
from .parsers import ParseError, build_tournament

HIDDEN_NAME = "Hidden player"
MEMBERSHIPS = ("", "supporter", "diamond")


class HttpError(Exception):
    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ladder(query: dict) -> str:
    ladder = query.get("ladder") or "rel"
    if ladder not in LADDERS:
        raise HttpError(400, f"ladder must be one of {', '.join(LADDERS)}")
    return ladder


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
        add("DELETE", r"/tournaments/(?P<tid>[\w-]+)", self.delete_tournament, "admin")
        add("POST", "/admin/recalculate", lambda **_: self.recalculate(), "admin")
        add("POST", "/release-notes", self.publish_release_note, "admin")
        add("GET", "/admin/claims", self.list_claims, "admin")
        add("POST", r"/admin/claims/(?P<sub>[\w-]+)", self.review_claim, "admin")
        add("POST", r"/admin/players/(?P<pid>[0-9a-f]{12})", self.admin_update_player, "admin")
        add("GET", "/me", self.me, "user")
        add("POST", "/me/claim", self.claim, "user")
        add("POST", "/me/privacy", self.privacy, "user")

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
        return {"tournaments": len(tournaments), "players": len(result["names"]),
                "modelVersion": self.cfg.to_dict()["modelVersion"]}

    # --- public ------------------------------------------------------------
    def leaderboard(self, query, **_):
        ladder = _ladder(query)
        rows = self.storage.leaderboard(ladder)
        for i, row in enumerate(rows, start=1):
            row["rank"] = i
            if row["hidden"]:
                row["displayName"] = HIDDEN_NAME
        return {"ladder": ladder, "players": rows}

    def player(self, pid, query, **_):
        ladder = _ladder(query)
        entry = self.storage.get_ladder_entry(pid, ladder)
        if not entry:
            others = [l for l in LADDERS if l != ladder and self.storage.get_ladder_entry(pid, l)]
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
        board = self.storage.leaderboard(ladder)
        rank = next((i for i, r in enumerate(board, start=1) if r["playerId"] == pid), None)
        return {
            "playerId": pid, "ladder": ladder, "rank": rank, "playerCount": len(board),
            "displayName": HIDDEN_NAME if entry["hidden"] else entry["displayName"],
            "hidden": entry["hidden"], "membership": entry.get("membership", ""),
            "rating": entry["rating"], "stats": stats, "tournaments": entry["tournaments"],
        }

    def player_history(self, pid, query, **_):
        ladder = _ladder(query)
        history = self.storage.get_history(pid, ladder)
        names = self._names(h["opponentId"] for h in history)
        for h in history:
            h["opponentName"] = names.get(h["opponentId"], "?")
        return {"playerId": pid, "ladder": ladder, "initialRating": self.cfg.initial_rating, "history": history}

    def tournaments(self, **_):
        return [{k: v for k, v in m.items() if k not in ("s3Key", "rawKey")} for m in self.storage.list_tournaments()]

    def tournament(self, tid, **_):
        t = self.storage.get_tournament(tid)
        if not t:
            raise HttpError(404, "Tournament not found")
        names = self._names(t["players"])
        t["players"] = {pid: names.get(pid, name) for pid, name in t["players"].items()}
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
        t = build_tournament(
            content=body.get("content", ""), name=body.get("name", ""), date=body.get("date", ""),
            type=body.get("type", ""), fmt=body.get("format", "auto"),
            uploaded_by=claims.get("email", claims["sub"]), uploaded_at=_now(),
        )
        known = self.storage.get_profiles(t["players"])
        new_players = sorted(name for pid, name in t["players"].items() if pid not in known)
        duplicate = any(m["tournamentId"] == t["id"] for m in self.storage.list_tournaments())
        if query.get("dryRun") in ("1", "true"):
            return {"dryRun": True, "tournament": t, "newPlayers": new_players, "duplicate": duplicate}
        if duplicate:
            raise HttpError(409, "This exact tournament has already been uploaded", tournamentId=t["id"])
        ext = {"csv": "csv", "json": "json"}.get(t["sourceFormat"], "txt")
        self.storage.save_tournament(t, body["content"], ext)
        return {"tournamentId": t["id"], "newPlayers": new_players, "recalculation": self.recalculate()}

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

    def privacy(self, claims, body, **_):
        account = self._account(claims)
        if account["claimStatus"] != "approved":
            raise HttpError(403, "Your player profile claim must be approved first")
        return self.storage.update_profile(account["playerId"], hidden=bool(body.get("hidden")))


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
