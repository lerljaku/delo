"""HTTP API. `App.handle` is transport-agnostic; `lambda_handler` adapts API Gateway
HTTP API (payload v2) events, and backend/local_server.py adapts http.server.
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import threading
import traceback
from collections.abc import Callable
from datetime import date as date_cls, datetime, timedelta, timezone

from . import engine, payments, premium, privacy, schedule, stats
from .elo import DEFAULT_CONFIG, GAME_FORMATS, LADDERS, ladder_id, ladders_for
from .parsers import ParseError, _slug, build_tournament, parse_game_format, parse_link, upload_content
from .storage import PLAYER_VALUES, UPCOMING_KEEP_DAYS

HIDDEN_NAME = "Hidden player"
MEMBERSHIPS = ("", "supporter", "diamond")
EVENTLINK = "https://eventlink.wizards.com"
EXTENSION_UPLOADER = "Eventlink extension (anonymous)"  # uploadedBy of POST /api/eventlink
JOB_MAX_BYTES = 350_000  # a job is one DynamoDB item (at most 400 KB)
# a job not done after this long never will be: queued = never started, running > Lambda timeout (180 s)
JOB_STUCK_SECONDS = {"queued": 300, "running": 240}


def _eventlink_event(link: str | None) -> str | None:
    """The event number of an Eventlink event link, e.g. …/stores/11890/events/12056192/rounds/1 -> 12056192."""
    m = re.match(rf"^{re.escape(EVENTLINK)}/(?:stores/\d+/)?events/(\d+)(?:[/?#]|$)", link or "")
    return m and m[1]


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


def _rated(stats: dict) -> int:
    """Rated matches: wins + losses (draws and byes don't change ratings)."""
    return stats["wins"] + stats["losses"]


def _groups(claims: dict) -> set[str]:
    raw = claims.get("cognito:groups") or []
    if isinstance(raw, str):
        raw = raw.strip("[]").replace(",", " ").split()
    return set(raw)


class App:
    def __init__(self, storage, cfg=DEFAULT_CONFIG, stripe_secret: str | None = None, stats_secret: str | None = None,
                 background: Callable[[str], None] | None = None):
        self.storage = storage
        self.cfg = cfg
        self.stripe_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "") if stripe_secret is None else stripe_secret
        # key for the anonymous visitor codes (delo/stats.py); set in the Lambda by Terraform
        self.stats_secret = stats_secret or os.environ.get("STATS_SECRET") or secrets.token_hex(16)
        # injected: runs a background job (see run_job), by default in a thread of this process
        self.background = background or (lambda jid: threading.Thread(target=self.run_job, args=(jid,), daemon=True).start())
        r = self._routes = []
        # routes that may run as a background job (?async=1): they can recalculate every rating,
        # which takes longer than API Gateway waits (30 s)
        self._background = set()

        def add(m, p, f, auth=None, background=False):
            r.append((m, re.compile(f"^/api{p}$"), f, auth))
            if background:
                self._background.add((m, f"^/api{p}$"))
        add("GET", "/leaderboard", self.leaderboard)
        add("GET", r"/players/(?P<pid>[0-9a-f]{12})", self.player)
        add("GET", r"/players/(?P<pid>[0-9a-f]{12})/history", self.player_history)
        add("GET", "/tournaments", self.tournaments)
        add("GET", r"/tournaments/(?P<tid>[\w-]+)", self.tournament)
        add("GET", "/release-notes", self.release_notes)
        add("GET", r"/release-notes/(?P<version>[\w.-]+)", self.release_note)
        add("GET", "/elo-model", lambda **_: self.cfg.to_dict())
        add("GET", r"/players/by-slug/(?P<slug>[a-z0-9-]+)", self.player_by_slug)
        add("GET", "/upcoming", self.upcoming)
        add("GET", "/planned-tournaments", self.planned_tournaments)  # the Chrome extension: no sign-in
        add("GET", r"/jobs/(?P<jid>[0-9a-f]{32})", self.job)  # the id is the secret: 128 random bits
        add("GET", "/islands", self.islands)
        add("POST", "/stripe/webhook", self.stripe_webhook)  # authenticated by its signature
        add("POST", "/visit", self.record_visit)  # page-view beacon, see delo/stats.py
        add("POST", "/eventlink", self.eventlink_upload, background=True)  # the Chrome extension: anonymous, see eventlink_upload
        add("POST", "/tournaments", self.upload, "user", background=True)  # correcting (?replace=) is admin-only, see upload
        add("POST", r"/tournaments/(?P<tid>[\w-]+)", self.update_tournament, "admin", background=True)
        add("DELETE", r"/tournaments/(?P<tid>[\w-]+)", self.delete_tournament, "admin", background=True)
        add("POST", "/admin/recalculate", lambda **_: self.recalculate(), "admin", background=True)
        add("POST", "/release-notes", self.publish_release_note, "admin")
        add("GET", "/admin/claims", self.list_claims, "admin")
        add("POST", r"/admin/claims/(?P<sub>[\w-]+)", self.review_claim, "admin")
        add("POST", r"/admin/players/(?P<pid>[0-9a-f]{12})", self.admin_update_player, "admin")
        add("POST", r"/admin/players/(?P<pid>[0-9a-f]{12})/erase", self.erase_player, "admin", background=True)
        add("POST", r"/admin/players/(?P<pid>[0-9a-f]{12})/merge", self.merge_player, "admin", background=True)
        add("GET", "/me", self.me, "user")
        add("DELETE", "/me", self.delete_me, "user")
        add("POST", "/me/claim", self.claim, "user")
        add("POST", "/me/privacy", self.privacy, "user")
        add("GET", "/me/decks", self.my_decks, "user")
        add("POST", r"/me/decks/(?P<tid>[\w-]+)", self.set_my_deck, "user")
        add("POST", "/me/profile", self.update_my_profile, "user")
        add("GET", "/me/notes", self.my_notes, "user")
        add("POST", r"/me/notes/(?P<tid>[\w-]+)", self.set_my_note, "user")
        add("GET", "/me/archetypes", self.my_archetypes, "user")
        add("GET", "/me/export", self.my_export, "user")
        add("GET", "/me/upcoming", self.my_upcoming, "user")
        add("GET", "/me/planned-tournaments", self.planned_tournaments, "user")
        add("GET", r"/admin/tournaments/(?P<tid>[\w-]+)", self.tournament_upload_info, "admin")
        add("GET", "/admin/stats", self.site_stats, "admin")
        add("GET", "/admin/islands", self.admin_islands, "admin")
        add("POST", "/admin/islands", self.create_island, "admin")
        add("POST", r"/admin/islands/(?P<iid>[a-z0-9-]+)", self.rename_island, "admin")
        add("POST", "/upcoming", self.add_upcoming, "user")
        add("DELETE", r"/upcoming/(?P<eid>[0-9a-f]+)", self.delete_upcoming, "user")

    # --- dispatch ----------------------------------------------------------
    def handle(self, method: str, path: str, query: dict, body: dict, claims: dict,
               raw: str = "", headers: dict | None = None) -> tuple[int, dict | list]:
        """raw (the unparsed body) and headers are only needed to verify webhook signatures."""
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        try:
            for m, pattern, fn, auth in self._routes:
                match = pattern.match(path)
                if not match or m != method:
                    continue
                if auth and not claims.get("sub"):
                    raise HttpError(401, "Sign in required")
                if auth == "admin" and "admin" not in _groups(claims):
                    raise HttpError(403, "Admin only")
                if (query.get("async") in ("1", "true") and (m, pattern.pattern) in self._background
                        and query.get("dryRun") not in ("1", "true")):
                    return 202, self._start_job(method, path, query, body, claims)
                return 200, fn(query=query, body=body, claims=claims, raw=raw, headers=headers, **match.groupdict())
            if any(p.match(path) for _, p, _, _ in self._routes):
                raise HttpError(405, "Method not allowed")
            raise HttpError(404, "Not found")
        except HttpError as e:
            return e.status, {"error": e.message, **e.extra}
        except (ParseError, premium.Invalid) as e:
            return 400, {"error": str(e)}

    # --- background jobs ---------------------------------------------------
    def _start_job(self, method: str, path: str, query: dict, body: dict, claims: dict) -> dict:
        """Save the request as a job and start it in the background; the client polls GET /api/jobs/<id>."""
        request = {"method": method, "path": path, "query": {k: v for k, v in query.items() if k != "async"},
                   "body": body, "claims": claims}
        if len(json.dumps(request)) > JOB_MAX_BYTES:
            raise HttpError(413, "This upload is too large")
        jid = secrets.token_hex(16)
        self.storage.put_job(jid, {"status": "queued", "request": request, "createdAt": _now()})
        self.background(jid)
        return {"jobId": jid, "status": "queued"}

    def run_job(self, jid: str) -> None:
        """Answer a job's request as handle would have. Runs once per job, also if invoked again
        (Lambda may retry an asynchronous invocation)."""
        job = self.storage.start_job(jid, _now())
        if not job:
            return
        r = job["request"]
        try:
            status, result = self.handle(r["method"], r["path"], r["query"], r["body"], r["claims"])
        except Exception:  # noqa: BLE001 - the job must end, or the client waits until it looks stuck
            traceback.print_exc()
            status, result = 500, {"error": "Internal server error"}
        self.storage.finish_job(jid, status, result, _now())

    def job(self, jid, **_):
        """A background request: status queued|running|done; when done, statusCode and result are
        what the request answered. A job that stopped reporting (e.g. a Lambda timeout) is done with an error."""
        job = self.storage.get_job(jid)
        if not job:
            raise HttpError(404, "Job not found")
        if job["status"] == "done":
            return {"jobId": jid, "status": "done", "statusCode": job["statusCode"], "result": job["result"]}
        since = datetime.strptime(job.get("startedAt") or job["createdAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - since > timedelta(seconds=JOB_STUCK_SECONDS[job["status"]]):
            return {"jobId": jid, "status": "done", "statusCode": 500,
                    "result": {"error": "The server didn't finish this in time. Check whether it took effect before trying again."}}
        return {"jobId": jid, "status": job["status"]}

    # --- helpers -----------------------------------------------------------
    def _names(self, pids) -> dict[str, str]:
        pids = {p for p in pids if p}
        profiles = self.storage.get_profiles(pids)
        return {pid: (HIDDEN_NAME if p.get("hidden") else p.get("displayName", "?")) for pid, p in profiles.items()}

    def _island(self, query) -> str:
        """The selected island (?island=, sent by the site from its cookie). Unknown or missing:
        the default, i.e. the oldest island; "" while no island exists (everything is one group)."""
        ids = [i["islandId"] for i in self.storage.list_islands()]
        if not ids:
            return ""
        return query.get("island") if query.get("island") in ids else ids[0]

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
        # every rated player has an "all" entry; other profiles belong to deleted tournaments
        # only (or to other islands: their spellings carry over, which is fine)
        profiles = self.storage.get_profiles(prior[ladder_id("all", island=t.get("island") or "")])
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
            row["provisional"] = _rated(row["summary"]) < self.cfg.min_matches
            if row["hidden"]:
                row["displayName"] = HIDDEN_NAME
        ranked = [r for r in rows if not r["provisional"]]
        for i, row in enumerate(ranked, start=1):
            row["rank"] = i
        provisional = sorted((r for r in rows if r["provisional"]),
                             key=lambda r: (-_rated(r["summary"]), r["displayName"].casefold()))
        for row in provisional:
            row["rank"] = row["rating"] = row["summary"]["peakRating"] = None
        return ranked + provisional

    def leaderboard(self, query, **_):
        ladder, game_format = _ladder(query)
        island = self._island(query)
        rows = self._ranked(ladder_id(ladder, game_format, island))
        # flair lives on the profile; hidden players show none (it could identify them)
        profiles = self.storage.get_profiles(r["playerId"] for r in rows if not r["hidden"])
        for row in rows:
            prof = profiles.get(row["playerId"], {})
            row["flair"], row["flairColor"] = prof.get("flair") or "", prof.get("flairColor") or ""
        return {"ladder": ladder, "format": game_format, "island": island, "minMatches": self.cfg.min_matches, "players": rows}

    def player(self, pid, query, **_):
        ladder, game_format = _ladder(query)
        island = self._island(query)
        entry = self.storage.get_ladder_entry(pid, ladder_id(ladder, game_format, island))
        if not entry:
            others = [l for l in LADDERS if l != ladder and self.storage.get_ladder_entry(pid, ladder_id(l, game_format, island))]
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
        board = [r for r in self._ranked(ladder_id(ladder, game_format, island)) if not r["provisional"]]
        rank = next((r["rank"] for r in board if r["playerId"] == pid), None)
        return {
            "playerId": pid, "ladder": ladder, "format": game_format, "island": island, "rank": rank, "playerCount": len(board),
            "provisional": _rated(stats) < self.cfg.min_matches, "minMatches": self.cfg.min_matches,
            "displayName": HIDDEN_NAME if entry["hidden"] else entry["displayName"],
            "hidden": entry["hidden"], "membership": entry.get("membership", ""),
            "rating": entry["rating"], "stats": stats, "tournaments": self._with_decks(pid, entry["tournaments"]),
            **self._public_flair(pid, entry["hidden"]),
        }

    def _public_flair(self, pid: str, hidden: bool) -> dict:
        """Profile flair and Diamond profile URL; hidden players show neither (they could identify them)."""
        prof = {} if hidden else self.storage.get_profiles([pid]).get(pid, {})
        return {"flair": prof.get("flair") or "", "flairColor": prof.get("flairColor") or "", "slug": prof.get("slug") or ""}

    def _with_decks(self, pid: str, rows: list[dict]) -> list[dict]:
        metas = {m["tournamentId"]: m for m in self.storage.list_tournaments()}
        return [{**r, "deck": (metas.get(r["tournamentId"], {}).get("decks") or {}).get(pid)} for r in rows]

    def player_by_slug(self, slug, **_):
        """Custom profile URL (Diamond): player.html?u=<slug>."""
        pid = self.storage.resolve_slug(slug)
        prof = self.storage.get_profiles([pid]).get(pid) if pid else None
        if not prof or prof.get("hidden") or prof.get("slug") != slug:
            raise HttpError(404, "No player with this profile URL")
        return {"playerId": pid}

    def player_history(self, pid, query, **_):
        ladder, game_format = _ladder(query)
        history = self.storage.get_history(pid, ladder_id(ladder, game_format, self._island(query)))
        names = self._names(h["opponentId"] for h in history)
        for h in history:
            h["opponentName"] = names.get(h["opponentId"], "?")
        return {"playerId": pid, "ladder": ladder, "format": game_format, "initialRating": self.cfg.initial_rating,
                "history": history}

    def tournaments(self, query, **_):
        """The selected island's tournaments."""
        island = self._island(query)
        return [{k: v for k, v in m.items() if k not in ("s3Key", "rawKey", "decks", "uploadedBy")}
                for m in self.storage.list_tournaments() if (m.get("island") or "") == island]

    def tournament_upload_info(self, tid, **_):
        """Who uploaded a tournament (an email, so admins only), e.g. to check a suspicious one."""
        meta = next((m for m in self.storage.list_tournaments() if m["tournamentId"] == tid), None)
        if not meta:
            raise HttpError(404, "Tournament not found")
        return {"tournamentId": tid, "uploadedBy": meta.get("uploadedBy"), "uploadedAt": meta.get("uploadedAt")}

    def tournament(self, tid, **_):
        t = self.storage.get_tournament(tid)
        if not t:
            raise HttpError(404, "Tournament not found")
        names = self._names(t["players"])
        t["players"] = {pid: names.get(pid, name) for pid, name in t["players"].items()}
        t.pop("uploadedBy", None)  # personal data; admins: GET /api/admin/tournaments/{id}
        # Elo change of each player in this tournament, in the ladder its player links open:
        # REL or REL + Casual by type, limited to its format when it has one
        ladder = "rel" if t["type"] == "rel" else "all"
        lid = ladder_id(ladder, t.get("format"), t.get("island") or "")
        t["ratingLadder"] = {"ladder": ladder, "format": t.get("format") or ""}
        meta = next((m for m in self.storage.list_tournaments() if m["tournamentId"] == tid), {})
        t["decks"] = meta.get("decks") or {}
        t["ratingChanges"] = {
            pid: {k: row[k] for k in ("ratingBefore", "ratingAfter", "delta")}
            for pid, entry in self.storage.get_ladder_entries([lid], t["players"])[lid].items()
            if _rated(entry["stats"]) >= self.cfg.min_matches  # provisional players show no Elo
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
        t, content, ext, new_players, merged, erased = self._prepare_upload(query, body, claims.get("email", claims["sub"]))
        stored = self.storage.list_tournaments()
        # ?replace=<tournamentId>: corrected results for an uploaded tournament (see _replace)
        replaced = next((m for m in stored if m["tournamentId"] == query.get("replace")), None)
        if query.get("replace") and "admin" not in _groups(claims):
            raise HttpError(403, "Only admins can replace a tournament's results")
        if query.get("replace") and not replaced:
            raise HttpError(404, "The tournament to replace was not found")
        if replaced:  # corrected results stay on the original tournament's island
            t["island"] = replaced.get("island")
        others = [m for m in stored if m is not replaced]
        duplicate = any(m["tournamentId"] == t["id"] for m in others)
        # a recurring tournament keeps one link for every date: only the same date is suspicious
        recurring = t["link"] and any(e.get("recurrence") and e.get("link") == t["link"] for e in self.storage.list_upcoming())
        same_link = [{"tournamentId": m["tournamentId"], "name": m["name"], "date": m["date"]}
                     for m in others if t["link"] and m.get("link") == t["link"] and (not recurring or m["date"] == t["date"])]
        if query.get("dryRun") in ("1", "true"):
            return {"dryRun": True, "tournament": t, "newPlayers": new_players, "duplicate": duplicate,
                    "sameLink": same_link, "erasedPlayers": erased, "mergedNames": merged,
                    "unchanged": bool(replaced) and replaced["tournamentId"] == t["id"]}
        if duplicate:
            raise HttpError(409, "This exact tournament has already been uploaded", tournamentId=t["id"])
        if replaced:
            return self._replace(replaced, t, content, ext, new_players, same_link, erased)
        self.storage.save_tournament(t, content, ext)
        return {"tournamentId": t["id"], "newPlayers": new_players, "sameLink": same_link,
                "erasedPlayers": erased, "recalculation": self.rate_new_tournament(t, stored)}

    def eventlink_upload(self, query, body, **_):
        """Anonymous upload from the ESL Chrome extension (extension/): results read from an Eventlink
        event page. Body as for upload, with the Eventlink copied-pairings paste as content, plus
        eventId and storeId from the event's URL (they make the link). One tournament per Eventlink
        event, so clicking Upload again is harmless: the same results change nothing, different
        results (e.g. a round more) replace the earlier upload."""
        event_id, store_id = str(body.get("eventId") or ""), str(body.get("storeId") or "")
        if not re.fullmatch(r"\d{1,12}", event_id) or not re.fullmatch(r"\d{0,12}", store_id):
            raise HttpError(400, "eventId (and storeId) must be the numbers from the Eventlink event URL")
        link = f"{EVENTLINK}/{f'stores/{store_id}/' if store_id else ''}events/{event_id}"
        t, content, ext, new_players, _, erased = self._prepare_upload(
            query, {**body, "files": None, "sourceFormat": "eventlink", "link": link}, EXTENSION_UPLOADER)
        stored = self.storage.list_tournaments()
        claimed = self.storage.get_eventlink(event_id)
        # the event's earlier upload: by the extension, else one from the upload form linking to the event
        old = next((m for m in stored if m["tournamentId"] == claimed), None) if claimed else \
            next((m for m in stored if _eventlink_event(m.get("link")) == event_id), None)
        same = next((m for m in stored if m["tournamentId"] == t["id"]), None)
        if same:  # these exact results are in already
            if claimed != t["id"]:
                self.storage.claim_eventlink(event_id, t["id"], claimed)
            return {"tournamentId": t["id"], "unchanged": True}
        if not self.storage.claim_eventlink(event_id, t["id"], claimed):
            # an upload of this event running at the same time got there first
            if self.storage.get_eventlink(event_id) == t["id"]:
                return {"tournamentId": t["id"], "unchanged": True}
            raise HttpError(409, "This event is being uploaded at the same time. Try again in a minute.")
        if old:  # corrected results stay on the earlier upload's island
            t["island"] = old.get("island")
            return {**self._replace(old, t, content, ext, new_players, [], erased), "updated": True}
        self.storage.save_tournament(t, content, ext)
        return {"tournamentId": t["id"], "newPlayers": new_players, "erasedPlayers": erased,
                "recalculation": self.rate_new_tournament(t, stored)}

    def _prepare_upload(self, query, body, uploaded_by: str) -> tuple[dict, str, str, list[str], list[str], int]:
        """The tournament an upload describes, with known misspellings merged and erased players anonymized:
        (tournament, raw content to store, its file extension, new player names, merged names, erased count)."""
        content, source_format = upload_content(body.get("content", ""), body.get("files"),
                                                body.get("sourceFormat", "auto"))
        t = build_tournament(
            content=content, name=body.get("name", ""), date=body.get("date", ""),
            type=body.get("type", ""), fmt=source_format,
            game_format=body.get("format", ""), link=body.get("link", ""),
            uploaded_by=uploaded_by, uploaded_at=_now(),
        )
        t["island"] = self._island(query) or None
        if not t["format"]:
            raise HttpError(400, f"Format is required: one of {', '.join(GAME_FORMATS.values())}")
        ext = {"csv": "csv", "json": "json"}.get(t["sourceFormat"], "txt")
        # a misspelling an admin merged into another player goes to that player (unless both names
        # are in this tournament: then they are two people)
        merged = []
        aliases = self.storage.get_aliases(t["players"])
        targets = self.storage.get_profiles(aliases.values())
        for pid, target in aliases.items():
            if target not in t["players"] and target in targets:
                merged.append(f"{t['players'][pid]} → {targets[target]['displayName']}")
                t = privacy.replace_player(t, pid, target, targets[target]["displayName"])
        # players who had their data erased stay anonymous in new uploads too
        erased = self.storage.erased_ids(t["players"])
        # a misspelling of a player who was merged and later erased stays anonymous as well
        erased |= {pid for pid, target in aliases.items() if pid in t["players"] and target in self.storage.erased_ids(aliases.values())}
        for pid in erased:
            spelling = t["players"][pid]
            t = privacy.anonymize(t, pid, privacy.new_anonymous_id())
            content = content and privacy.scrub_raw(content, pid, [spelling], t["sourceFormat"])
        if not content:  # a name could not be removed from the raw text: store the normalized form only
            content, ext = json.dumps(t), "json"
        known = self.storage.get_profiles(t["players"])
        new_players = sorted(name for pid, name in t["players"].items()
                             if pid not in known and name != privacy.ERASED_NAME)
        return t, content, ext, new_players, merged, len(erased)

    def _replace(self, old: dict, t: dict, content: str, ext: str, new_players, same_link, erased: int) -> dict:
        """Swap a tournament for corrected results: save the new one, carry over the decks of
        players who are still in it, delete the old one, recalculate everything
        (removing matches changes the ratings computed after them)."""
        if old["tournamentId"] == t["id"]:
            raise HttpError(409, "These are the same results as the tournament you're replacing. "
                                 "To change only its format or link, use the form on the tournament page.")
        self.storage.save_tournament(t, content, ext)
        for field in PLAYER_VALUES:
            for pid, value in (old.get(field) or {}).items():
                if pid in t["players"]:
                    self.storage.set_player_value(t["id"], field, pid, value)
        self.storage.delete_tournament(old["tournamentId"])
        return {"tournamentId": t["id"], "replaced": old["tournamentId"], "newPlayers": new_players,
                "sameLink": same_link, "erasedPlayers": erased, "recalculation": self.recalculate()}

    def update_tournament(self, tid, body, **_):
        """Change the game format, link and/or island of an uploaded tournament."""
        fields = {}
        if "format" in body:
            fields["format"] = parse_game_format(body["format"])
        if "link" in body:
            fields["link"] = parse_link(body["link"])
        if "island" in body:
            if body["island"] not in {i["islandId"] for i in self.storage.list_islands()}:
                raise HttpError(400, "Unknown island")
            fields["island"] = body["island"]
        before = self.storage.get_tournament(tid)
        if not before:
            raise HttpError(404, "Tournament not found")
        t = self.storage.update_tournament(tid, **fields)
        changed = any(t.get(k) != before.get(k) for k in ("format", "island"))
        recalculation = self.recalculate() if changed else None
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

    def merge_player(self, pid, body, query, **_):
        """Merge player `pid` (e.g. a misspelled name) into body.into: in every tournament the
        player becomes `into` (results, standings, name), their decks move along, the duplicate
        player is removed, and the misspelling is remembered so later uploads use `into` too.
        ?dryRun=1 only lists the tournaments that would change. Refused when both players are in
        the same tournament (two different people) or when `pid` has a claimed profile."""
        into = str(body.get("into") or "")
        if into == pid:
            raise HttpError(400, "Choose two different players")
        profiles = self.storage.get_profiles([pid, into])
        if pid not in profiles or into not in profiles:
            raise HttpError(404, "Player not found")
        source, target = profiles[pid], profiles[into]
        if source.get("ownerSub"):
            raise HttpError(409, f"{source['displayName']} has a claimed profile. Merge the other way: "
                                 f"{target['displayName']} into {source['displayName']}.")
        tournaments = [t for t in self.storage.load_all_tournaments() if pid in t["players"]]
        row = lambda t: {"tournamentId": t["id"], "name": t["name"], "date": t["date"]}  # noqa: E731
        conflicts = [row(t) for t in tournaments if into in t["players"]]
        summary = {"from": source["displayName"], "into": target["displayName"],
                   "tournaments": [row(t) for t in sorted(tournaments, key=lambda t: t["date"])], "conflicts": conflicts}
        if query.get("dryRun") in ("1", "true"):
            return summary
        if conflicts:
            raise HttpError(409, "Both players played in the same tournament, so they are two different people",
                            conflicts=conflicts)
        metas = {m["tournamentId"]: m for m in self.storage.list_tournaments()}
        for t in tournaments:
            raw, ext = self.storage.get_raw(t["id"])  # the uploaded file stays as it was
            self.storage.replace_tournament(privacy.replace_player(t, pid, into, target["displayName"]), raw, ext)
            deck = (metas[t["id"]].get("decks") or {}).get(pid)
            if deck:
                self.storage.set_deck(t["id"], into, deck)
                self.storage.set_deck(t["id"], pid, None)
        if source.get("membership") and not target.get("membership"):  # e.g. 💎 given to the misspelling
            self.storage.update_profile(into, membership=source["membership"])
        self.storage.delete_player(pid)
        self.storage.set_alias(pid, into)
        return {**summary, "merged": True, "recalculation": self.recalculate() if tournaments else None}

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
        if (profile or {}).get("slug"):
            self.storage.release_slug(profile["slug"], pid)
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

    def _level(self, claims, account: dict | None = None) -> str:
        """Membership that unlocks perks: paid (account), or granted by an admin on the claimed
        profile, whichever is higher. Admins get everything."""
        if "admin" in _groups(claims):
            return "diamond"
        account = account or self._account(claims)
        level = account.get("membership") or ""
        if account.get("claimStatus") == "approved" and account.get("playerId"):
            prof = self.storage.get_profiles([account["playerId"]]).get(account["playerId"]) or {}
            level = premium.best_level(level, prof.get("membership"))
        return level

    def _require(self, claims, required: str, feature: str) -> None:
        if not premium.at_least(self._level(claims), required):
            raise HttpError(403, f"{feature} needs a {required.title()} membership", requiredMembership=required)

    def me(self, claims, **_):
        account = self._account(claims)
        player = None
        if account.get("playerId"):
            player = self.storage.get_profiles([account["playerId"]]).get(account["playerId"])
        return {**account, "isAdmin": "admin" in _groups(claims), "player": player,
                "effectiveMembership": self._level(claims, account)}

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

    def my_decks(self, claims, query, **_):
        """The signed-in player's tournaments (newest first) with their decks, and deck name
        suggestions per format: the player's own decks, most recently played first, then the
        other decks entered for that format, alphabetically."""
        pid = self._owned_player(claims)
        # the island's "all" ladder has every tournament the player played there
        entry = self.storage.get_ladder_entry(pid, ladder_id("all", island=self._island(query))) or {"tournaments": []}
        metas = {m["tournamentId"]: m for m in self.storage.list_tournaments()}
        rows = [{k: t.get(k) for k in ("tournamentId", "name", "date", "format", "type")}
                | {"deck": (metas.get(t["tournamentId"], {}).get("decks") or {}).get(pid)}
                for t in entry["tournaments"]]
        return {"playerId": pid, "tournaments": rows, "suggestions": _deck_suggestions(metas.values(), pid),
                "membership": self._level(claims)}

    def _played(self, claims, tid) -> tuple[str, dict]:
        pid = self._owned_player(claims)
        t = self.storage.get_tournament(tid)
        if not t or pid not in t["players"]:
            raise HttpError(404, "You didn't play in this tournament")
        return pid, t

    def set_my_deck(self, claims, tid, body, **_):
        pid, t = self._played(claims, tid)
        deck = " ".join(str(body.get("deck") or "").split())
        if len(deck) > 60 or any(not c.isprintable() for c in deck):
            raise HttpError(400, "Deck name must be at most 60 printable characters")
        # reuse an existing spelling ("kroxa" -> "Kroxa") so the same deck isn't listed twice
        known = {d.casefold(): d for m in self.storage.list_tournaments() if m.get("format") == t.get("format")
                 for d in (m.get("decks") or {}).values()}
        deck = known.get(deck.casefold(), deck) or None
        self.storage.set_deck(tid, pid, deck)
        return {"tournamentId": tid, "deck": deck}

    # --- membership perks ----------------------------------------------------
    def update_my_profile(self, claims, body, **_):
        """flair + flairColor (anyone with an approved claim), slug = custom profile URL (Diamond).
        Sending an empty slug removes it, which needs no membership."""
        pid = self._owned_player(claims)
        prof = self.storage.get_profiles([pid]).get(pid) or {}
        fields = {}
        if "flair" in body or "flairColor" in body:
            fields["flair"] = premium.text(body.get("flair", prof.get("flair")), "Flair", 30)
            fields["flairColor"] = premium.flair_color(body.get("flairColor", prof.get("flairColor")))
        if "slug" in body:
            new = premium.slug(body["slug"])
            old = prof.get("slug") or ""
            if new and new != old:
                self._require(claims, "diamond", "A custom profile URL")
                if not self.storage.claim_slug(new, pid):
                    raise HttpError(409, "This profile URL is already taken")
            if old and old != new:
                self.storage.release_slug(old, pid)
            fields["slug"] = new
        return self.storage.update_profile(pid, **fields)

    def my_notes(self, claims, **_):
        """Private match notes, {tournamentId: {round: note}}. Readable after a membership ends."""
        return {"notes": self.storage.get_notes(self._owned_player(claims))}

    def set_my_note(self, claims, tid, body, **_):
        pid, t = self._played(claims, tid)
        try:
            rnd = int(body.get("round"))
        except (TypeError, ValueError):
            raise HttpError(400, "round must be a number") from None
        if rnd not in {m["round"] for m in t["matches"] if pid in (m["p1"], m["p2"])}:
            raise HttpError(400, "You didn't play in this round")
        note = str(body.get("note") or "").strip()
        if len(note) > 1000 or any(not (c.isprintable() or c == "\n") for c in note):
            raise HttpError(400, "Note must be at most 1000 characters")
        if note:
            self._require(claims, "diamond", "Match notes")
        self.storage.set_note(pid, tid, rnd, note or None)
        return {"tournamentId": tid, "round": rnd, "note": note}

    def _own_entry(self, claims, feature: str, query: dict) -> tuple[str, dict, dict]:
        """(playerId, "all"-ladder entry with decks, {tournamentId: decks}) for Diamond perks."""
        self._require(claims, "diamond", feature)
        pid = self._owned_player(claims)
        entry = self.storage.get_ladder_entry(pid, ladder_id("all", island=self._island(query))) or {"tournaments": [], "displayName": ""}
        entry["tournaments"] = self._with_decks(pid, entry["tournaments"])
        decks = {m["tournamentId"]: m.get("decks") or {} for m in self.storage.list_tournaments()}
        return pid, entry, decks

    def my_archetypes(self, claims, query, **_):
        _, entry, decks = self._own_entry(claims, "Matchup stats by archetype", query)
        return premium.archetype_stats(entry["tournaments"], decks)

    def my_export(self, claims, query, **_):
        """Own match history as CSV (returned inside JSON; the page saves it as a file)."""
        pid, entry, decks = self._own_entry(claims, "CSV export", query)
        names = self._names(m["opponentId"] for t in entry["tournaments"] for m in t["matches"])
        content = premium.history_csv(entry["tournaments"], self.storage.get_history(pid, ladder_id("all", island=self._island(query))), names, decks,
                                      self.storage.get_notes(pid), pid)
        slug = re.sub(r"[^a-z0-9]+", "-", entry["displayName"].lower()).strip("-") or "player"
        return {"filename": f"delo-{slug}-history.csv", "csv": content}

    # --- payments ----------------------------------------------------------
    def stripe_webhook(self, raw, headers, **_):
        """Stripe events → membership. checkout.session.completed: client_reference_id is the
        Cognito sub (the frontend adds it to the Payment Link); mode "subscription" = Diamond,
        "payment" = Supporter. customer.subscription.*: Diamond while the subscription is active."""
        if not self.stripe_secret:
            raise HttpError(503, "Payments are not configured")
        try:
            payments.verify_signature(raw, headers.get("stripe-signature", ""), self.stripe_secret)
        except payments.SignatureError as e:
            raise HttpError(400, str(e)) from None
        event = json.loads(raw)
        kind, obj = event.get("type", ""), event.get("data", {}).get("object", {})
        if kind == "checkout.session.completed":
            sub = obj.get("client_reference_id")
            if not sub:
                return {"received": True, "ignored": "no client_reference_id"}
            account = self.storage.get_account(sub) or {
                "sub": sub, "email": (obj.get("customer_details") or {}).get("email", ""), "playerId": None,
                "claimStatus": "none", "membership": "", "createdAt": _now()}
            account["stripeCustomerId"] = obj.get("customer") or account.get("stripeCustomerId")
            if obj.get("mode") == "subscription":
                account.update(diamond=True, stripeSubscriptionId=obj.get("subscription"))
            else:
                account["supporter"] = True
        elif kind.startswith("customer.subscription."):
            account = next((a for a in self.storage.list_accounts()
                            if a.get("stripeCustomerId") and a["stripeCustomerId"] == obj.get("customer")), None)
            if not account:
                return {"received": True, "ignored": "unknown customer"}
            account["diamond"] = kind != "customer.subscription.deleted" and obj.get("status") in payments.ACTIVE_SUBSCRIPTION
        else:
            return {"received": True, "ignored": kind}
        account["membership"] = payments.membership(account)
        self.storage.put_account(account)
        if account.get("claimStatus") == "approved" and account.get("playerId"):
            self.storage.update_profile(account["playerId"], membership=account["membership"])
        return {"received": True, "membership": account["membership"]}

    # --- islands ------------------------------------------------------------
    def islands(self, **_):
        """Islands for the picker in the site navigation, the default (oldest) first."""
        return [{"islandId": i["islandId"], "name": i["name"], "icon": i.get("icon") or ""} for i in self.storage.list_islands()]

    def admin_islands(self, **_):
        counts = {}
        for m in self.storage.list_tournaments():
            counts[m.get("island") or ""] = counts.get(m.get("island") or "", 0) + 1
        return [{**i, "tournaments": counts.get(i["islandId"], 0)} for i in self.storage.list_islands()]

    def create_island(self, body, **_):
        """Body: name, icon (optional emoji shown in the island picker, e.g. a flag)."""
        name = premium.text(body.get("name"), "Name", 60, required=True)
        iid = _slug(name)
        if iid in {i["islandId"] for i in self.storage.list_islands()}:
            raise HttpError(409, "An island with this name already exists")
        island = {"islandId": iid, "name": name, "icon": premium.icon(body.get("icon")), "createdAt": _now()}
        self.storage.put_island(island)
        return island

    def rename_island(self, iid, body, **_):
        """Change the name and/or icon (the id stays, so links and ratings keep working)."""
        island = next((i for i in self.storage.list_islands() if i["islandId"] == iid), None)
        if not island:
            raise HttpError(404, "Island not found")
        if "name" in body:
            island["name"] = premium.text(body.get("name"), "Name", 60, required=True)
        if "icon" in body:
            island["icon"] = premium.icon(body.get("icon"))
        self.storage.put_island(island)
        return island

    # --- visitor statistics --------------------------------------------------
    def record_visit(self, body, headers, **_):
        """Count a page view (see delo/stats.py). Always answers 200: the page doesn't care."""
        user_agent = headers.get("user-agent", "")
        page = stats.page_name(body.get("page"))
        if not page or stats.is_bot(user_agent) or headers.get("dnt") == "1" or headers.get("sec-gpc") == "1":
            return {"counted": False}
        day = self._today().isoformat()
        views = self.storage.count_visitor(day, stats.visitor_code(self.stats_secret, day, stats.client_ip(headers), user_agent))
        if views > stats.MAX_VIEWS_PER_VISITOR:
            return {"counted": False}
        self.storage.add_page_view(day, page, stats.referrer_host(body.get("referrer"), set()), new_visitor=views == 1)
        return {"counted": True}

    def site_stats(self, query, **_):
        """Daily visitors and page views for the last ?days= days (7-365, default 30), with totals
        and the most viewed pages and referring sites over that period."""
        try:
            days = min(365, max(7, int(query.get("days") or 30)))
        except ValueError:
            raise HttpError(400, "days must be a number") from None
        today = self._today()
        dates = [(today - timedelta(days=n)).isoformat() for n in range(days - 1, -1, -1)]
        data = self.storage.get_stats(dates[0], dates[-1])
        daily = [{"date": d, **data["days"].get(d, {"views": 0, "visitors": 0})} for d in dates]
        total = lambda rows, key: sum(r[key] for r in rows)  # noqa: E731
        top = lambda counts: [{"name": k, "views": v} for k, v in  # noqa: E731
                              sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]
        return {
            "days": daily,
            "totals": {span: {"visitors": total(rows, "visitors"), "views": total(rows, "views")}
                       for span, rows in (("today", daily[-1:]), ("last7Days", daily[-7:]), ("period", daily))},
            "pages": top(data["pages"]), "referrers": top(data["referrers"]),
        }

    # --- upcoming tournaments ------------------------------------------------
    @staticmethod
    def _today() -> date_cls:
        return datetime.now(timezone.utc).date()

    @staticmethod
    def _public_event(e: dict, day: date_cls | None = None) -> dict:
        """An event as shown to everyone. For recurring events `date` is the occurrence `day`,
        `startDate` the first one; `holiday` names a public holiday on that day."""
        day = day or date_cls.fromisoformat(e["date"])
        out = {k: e.get(k) for k in ("eventId", "name", "time", "location", "format", "type", "link", "recurrence")}
        return {**out, "date": day.isoformat(), "startDate": e["date"],
                "repeats": schedule.describe(date_cls.fromisoformat(e["date"]), e.get("recurrence")),
                "holiday": schedule.holiday(day) if e.get("recurrence") else None}

    def _future_events(self, island: str) -> list[dict]:
        """Raw events with their next date (recurring: only the next occurrence), soonest first."""
        today = self._today()
        found = []
        for e in self.storage.list_upcoming():
            if (e.get("island") or "") != island:
                continue
            day = schedule.next_occurrence(date_cls.fromisoformat(e["date"]), e.get("recurrence"), today)
            if day:
                found.append((day, e))
        found.sort(key=lambda de: (de[0], de[1].get("time") or "", de[1]["name"].casefold()))
        return [{**e, "_day": day} for day, e in found]

    def upcoming(self, query, **_):
        return [self._public_event(e, e["_day"]) for e in self._future_events(self._island(query))]

    def planned_tournaments(self, query, **_):
        """For the upload form: occurrences from the last 30 days and the next one of each event,
        oldest first (results are usually uploaded after the event). ?days=N (at most 30, the
        Chrome extension asks for 14): only the last N days, up to today."""
        today = self._today()
        try:
            days_back = min(max(int(query.get("days") or UPCOMING_KEEP_DAYS), 1), UPCOMING_KEEP_DAYS)
        except ValueError:
            raise HttpError(400, "days must be a number") from None
        frm = today - timedelta(days=days_back)
        island = self._island(query)
        rows = []
        for e in self.storage.list_upcoming():
            if (e.get("island") or "") != island:
                continue
            start = date_cls.fromisoformat(e["date"])
            days = schedule.occurrences(start, e.get("recurrence"), frm, today - timedelta(days=1))
            if (nxt := schedule.next_occurrence(start, e.get("recurrence"), today)) and (not query.get("days") or nxt == today):
                days.append(nxt)
            rows += [self._public_event(e, d) for d in days]
        return sorted(rows, key=lambda r: (r["date"], r.get("time") or "", r["name"].casefold()))

    def my_upcoming(self, claims, query, **_):
        """Whether the user may post upcoming tournaments, and which listed ones they posted."""
        return {"canPost": premium.at_least(self._level(claims), "diamond"),
                "eventIds": [e["eventId"] for e in self._future_events(self._island(query)) if e.get("createdBy") == claims["sub"]]}

    def add_upcoming(self, claims, body, query, **_):
        self._require(claims, "diamond", "Posting upcoming tournaments")
        fields = premium.upcoming_event(body, self._today(), parse_game_format, parse_link)
        if "admin" not in _groups(claims):
            mine = [e for e in self.storage.list_upcoming() if e.get("createdBy") == claims["sub"]
                    and schedule.next_occurrence(date_cls.fromisoformat(e["date"]), e.get("recurrence"), self._today())]
            if len(mine) >= premium.MAX_UPCOMING_PER_MEMBER:
                raise HttpError(400, f"You can list at most {premium.MAX_UPCOMING_PER_MEMBER} upcoming tournaments at a time")
        event = {"eventId": secrets.token_hex(8), **fields, "island": self._island(query) or None,
                 "createdBy": claims["sub"], "createdAt": _now()}
        self.storage.put_upcoming(event)
        return self._public_event(event, schedule.next_occurrence(date_cls.fromisoformat(event["date"]),
                                                                  event["recurrence"], self._today()))

    def delete_upcoming(self, claims, eid, **_):
        event = self.storage.get_upcoming(eid)
        if not event:
            raise HttpError(404, "Upcoming tournament not found")
        if event.get("createdBy") != claims["sub"] and "admin" not in _groups(claims):
            raise HttpError(403, "Only the person who posted it or an admin can remove it")
        self.storage.delete_upcoming(eid)
        return {"deleted": eid}

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
            _app = App(AwsStorage(), background=_invoke_self)
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


def _invoke_self(jid: str) -> None:
    """Run a background job in another invocation of this Lambda, without waiting for it."""
    import boto3

    boto3.client("lambda").invoke(FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"], InvocationType="Event",
                                  Payload=json.dumps({"jobId": jid}).encode())


def lambda_handler(event, context):
    if "jobId" in event and "requestContext" not in event:  # from _invoke_self, not API Gateway
        _get_app().run_job(event["jobId"])
        return {"jobId": event["jobId"]}
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
                                            event.get("queryStringParameters") or {}, body, claims,
                                            raw=raw, headers=event.get("headers") or {})
        return response(status, payload)
    except Exception:  # noqa: BLE001 - last-resort guard so clients always get JSON
        traceback.print_exc()
        return response(500, {"error": "Internal server error"})
