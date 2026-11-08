"""Persistence. LocalStorage (JSON files, for development/tests) and AwsStorage
(S3 + DynamoDB) implement the same interface; see ARCHITECTURE.md §3 for the layout.
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
from decimal import Decimal
from pathlib import Path


PROFILE_FIELDS = ("displayName", "hidden", "membership", "ownerSub", "flair", "flairColor", "slug")
SUMMARY_FIELDS = ("matches", "ratedMatches", "wins", "losses", "draws", "winrate", "peakRating")
TOURNAMENT_FIELDS = ("format", "link", "island")  # editable after upload
# Per-player values set after the upload, kept on the tournament table item: {playerId: value}
PLAYER_VALUES = ("decks",)
UPCOMING_PK = "_UPCOMING"  # players-table partition holding upcoming events (single-table)
UPCOMING_KEEP_DAYS = 30  # one-off events stay this long after their date, for the upload form
# Visitor statistics (delo/stats.py): daily totals under STATS_PK; the anonymous per-day visitor
# codes under VISITOR_PK are deleted after VISITOR_KEEP_DAYS (DynamoDB TTL).
STATS_PK, VISITOR_PK, VISITOR_KEEP_DAYS = "_STATS", "_VISITOR", 2
ISLANDS_PK = "_ISLANDS"  # islands (groups of players rated separately, see delo/elo.py)
# Background requests (App.run_job): players-table item "_JOB#<id>", deleted a day later (DynamoDB TTL)
JOB_PK, JOB_KEEP_SECONDS = "_JOB#", 86400


def _ladder_item(pid: str, ladder: str, entry: dict, name: str, profile: dict) -> dict:
    return {
        "playerId": pid,
        "ladder": ladder,
        "rating": entry["rating"],
        "displayName": name,
        "hidden": bool(profile.get("hidden", False)),
        "membership": profile.get("membership") or "",
        "summary": {k: entry["stats"][k] for k in SUMMARY_FIELDS},
        "stats": entry["stats"],
        "tournaments": entry["tournaments"],
        "state": entry["state"],
    }


def _leaderboard_row(item: dict) -> dict:
    return {k: item.get(k) for k in ("playerId", "displayName", "rating", "hidden", "membership", "summary")}


class LocalStorage:
    """Everything in a directory. Thread-safe enough for the single-process dev server."""

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        (self.root / "tournaments").mkdir(parents=True, exist_ok=True)
        (self.root / "raw").mkdir(exist_ok=True)
        (self.root / "release-notes").mkdir(exist_ok=True)
        self._db_path = self.root / "db.json"
        self._lock = threading.RLock()
        if self._db_path.exists():
            self._db = json.loads(self._db_path.read_text("utf-8"))
        else:
            self._db = {"tournaments": {}, "profiles": {}, "ladders": {}, "history": {},
                        "accounts": {}, "releaseNotes": {}}
            self._save()
        for section, empty in (("erased", []), ("notes", {}), ("slugs", {}), ("upcoming", {}), ("stats", {}), ("visitors", {}),
                               ("islands", {}), ("aliases", {}), ("eventlink", {}), ("jobs", {})):
            self._db.setdefault(section, empty)

    def _save(self) -> None:
        tmp = self._db_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._db, indent=1), "utf-8")
        tmp.replace(self._db_path)

    # --- tournaments -------------------------------------------------------
    def save_tournament(self, t: dict, raw_content: str, raw_ext: str) -> None:
        with self._lock:
            (self.root / "raw" / f"{t['id']}.{raw_ext}").write_text(raw_content, "utf-8")
            (self.root / "tournaments" / f"{t['id']}.json").write_text(json.dumps(t), "utf-8")
            old = self._db["tournaments"].get(t["id"])
            self._db["tournaments"][t["id"]] = tournament_meta(t, f"tournaments/{t['id']}.json",
                                                               f"raw/{t['id']}.{raw_ext}", old)
            self._save()

    def delete_tournament(self, tid: str) -> bool:
        with self._lock:
            meta = self._db["tournaments"].pop(tid, None)
            if not meta:
                return False
            for key in (meta["s3Key"], meta["rawKey"]):
                (self.root / key).unlink(missing_ok=True)
            self._save()
            return True

    def list_tournaments(self) -> list[dict]:
        return sorted((dict(m) for m in self._db["tournaments"].values()), key=lambda m: (m["date"], m["uploadedAt"]), reverse=True)

    def get_tournament(self, tid: str) -> dict | None:
        meta = self._db["tournaments"].get(tid)
        if not meta:
            return None
        return json.loads((self.root / meta["s3Key"]).read_text("utf-8"))

    def update_tournament(self, tid: str, **fields) -> dict | None:
        with self._lock:
            t = self.get_tournament(tid)
            if not t:
                return None
            t.update({k: v for k, v in fields.items() if k in TOURNAMENT_FIELDS})
            meta = self._db["tournaments"][tid]
            (self.root / meta["s3Key"]).write_text(json.dumps(t), "utf-8")
            self._db["tournaments"][tid] = tournament_meta(t, meta["s3Key"], meta["rawKey"], meta)
            self._save()
            return t

    def load_all_tournaments(self) -> list[dict]:
        return [self.get_tournament(tid) for tid in list(self._db["tournaments"])]

    def set_player_value(self, tid: str, field: str, pid: str, value: str | None) -> bool:
        """Set (or with None remove) a PLAYER_VALUES entry, e.g. the deck a player played."""
        with self._lock:
            meta = self._db["tournaments"].get(tid)
            if not meta:
                return False
            values = meta.setdefault(field, {})
            if value:
                values[pid] = value
            else:
                values.pop(pid, None)
            self._save()
            return True

    def set_deck(self, tid: str, pid: str, deck: str | None) -> bool:
        return self.set_player_value(tid, "decks", pid, deck)

    # --- private match notes (Diamond) -------------------------------------
    def get_notes(self, pid: str) -> dict[str, dict[str, str]]:
        """{tournamentId: {round: note}}"""
        return copy.deepcopy(self._db["notes"].get(pid, {}))

    def set_note(self, pid: str, tid: str, rnd: int, note: str | None) -> None:
        with self._lock:
            notes = self._db["notes"].setdefault(pid, {}).setdefault(tid, {})
            if note:
                notes[str(rnd)] = note
            else:
                notes.pop(str(rnd), None)
            self._save()

    # --- custom profile URLs (Diamond) -------------------------------------
    def claim_slug(self, slug: str, pid: str) -> bool:
        """Reserve a profile URL name; False if another player has it."""
        with self._lock:
            if self._db["slugs"].get(slug, pid) != pid:
                return False
            self._db["slugs"][slug] = pid
            self._save()
            return True

    def release_slug(self, slug: str, pid: str) -> None:
        with self._lock:
            if self._db["slugs"].get(slug) == pid:
                del self._db["slugs"][slug]
                self._save()

    def resolve_slug(self, slug: str) -> str | None:
        return self._db["slugs"].get(slug)

    # --- Eventlink events uploaded by the extension ----------------------------
    def get_eventlink(self, event_id: str) -> str | None:
        return self._db["eventlink"].get(event_id)

    def claim_eventlink(self, event_id: str, tid: str, expected: str | None) -> bool:
        """Point an Eventlink event at tournament `tid`, only if it still points at `expected`."""
        with self._lock:
            if self._db["eventlink"].get(event_id) != expected:
                return False
            self._db["eventlink"][event_id] = tid
            self._save()
            return True

    # --- background jobs (App.run_job) ---------------------------------------------
    def put_job(self, job_id: str, job: dict) -> None:
        """A new job: {status: "queued", request, createdAt}. Jobs older than a day are dropped."""
        with self._lock:
            now = time.time()
            self._db["jobs"] = {k: j for k, j in self._db["jobs"].items() if j["expiresAt"] > now}
            self._db["jobs"][job_id] = {**copy.deepcopy(job), "expiresAt": int(now) + JOB_KEEP_SECONDS}
            self._save()

    def get_job(self, job_id: str) -> dict | None:
        return copy.deepcopy(self._db["jobs"].get(job_id))

    def start_job(self, job_id: str, started_at: str) -> dict | None:
        """Mark a queued job running and return it; None if it is unknown or already started."""
        with self._lock:
            job = self._db["jobs"].get(job_id)
            if not job or job["status"] != "queued":
                return None
            job.update(status="running", startedAt=started_at)
            self._save()
            return copy.deepcopy(job)

    def finish_job(self, job_id: str, status_code: int, result, finished_at: str) -> None:
        with self._lock:
            self._db["jobs"][job_id].update(status="done", statusCode=status_code, result=result, finishedAt=finished_at)
            self._save()

    # --- upcoming tournaments ------------------------------------------------
    def list_upcoming(self) -> list[dict]:
        return copy.deepcopy(list(self._db["upcoming"].values()))

    def get_upcoming(self, eid: str) -> dict | None:
        return copy.deepcopy(self._db["upcoming"].get(eid))

    def put_upcoming(self, event: dict) -> None:
        with self._lock:
            self._db["upcoming"][event["eventId"]] = copy.deepcopy(event)
            self._save()

    def delete_upcoming(self, eid: str) -> None:
        with self._lock:
            self._db["upcoming"].pop(eid, None)
            self._save()

    # --- islands ------------------------------------------------------------
    def list_islands(self) -> list[dict]:
        """Oldest first: the first island is the default."""
        return sorted((dict(i) for i in self._db["islands"].values()), key=lambda i: (i["createdAt"], i["islandId"]))

    def put_island(self, island: dict) -> None:
        with self._lock:
            self._db["islands"][island["islandId"]] = dict(island)
            self._save()

    # --- visitor statistics --------------------------------------------------
    def count_visitor(self, day: str, code: str) -> int:
        """Page views by this anonymous visitor code today, including this one."""
        from datetime import date, timedelta

        with self._lock:
            visitors = self._db["visitors"]
            oldest = (date.fromisoformat(day) - timedelta(days=VISITOR_KEEP_DAYS)).isoformat()
            for key in [k for k in visitors if k.split("#")[0] < oldest]:
                del visitors[key]
            key = f"{day}#{code}"
            visitors[key] = visitors.get(key, 0) + 1
            self._save()
            return visitors[key]

    def add_page_view(self, day: str, page: str, referrer: str | None, new_visitor: bool) -> None:
        with self._lock:
            stats = self._db["stats"].setdefault(day, {"views": 0, "visitors": 0, "pages": {}, "referrers": {}})
            stats["views"] += 1
            stats["visitors"] += int(new_visitor)
            stats["pages"][page] = stats["pages"].get(page, 0) + 1
            if referrer:
                stats["referrers"][referrer] = stats["referrers"].get(referrer, 0) + 1
            self._save()

    def get_stats(self, first: str, last: str) -> dict:
        """{"days": {day: {views, visitors}}, "pages": {page: views}, "referrers": {host: views}}"""
        out = {"days": {}, "pages": {}, "referrers": {}}
        for day, s in self._db["stats"].items():
            if first <= day <= last:
                out["days"][day] = {"views": s["views"], "visitors": s["visitors"]}
                for section in ("pages", "referrers"):
                    for k, n in s[section].items():
                        out[section][k] = out[section].get(k, 0) + n
        return out

    def get_raw(self, tid: str) -> tuple[str, str] | None:
        """(raw upload, file extension)."""
        meta = self._db["tournaments"].get(tid)
        if not meta:
            return None
        return (self.root / meta["rawKey"]).read_text("utf-8"), meta["rawKey"].rsplit(".", 1)[-1]

    def replace_tournament(self, t: dict, raw_content: str, raw_ext: str) -> None:
        """Overwrite a stored tournament and its raw upload, leaving no copy of the old content."""
        with self._lock:
            old_raw = self._db["tournaments"][t["id"]]["rawKey"]
            self.save_tournament(t, raw_content, raw_ext)
            if old_raw != self._db["tournaments"][t["id"]]["rawKey"]:
                (self.root / old_raw).unlink(missing_ok=True)

    # --- erasure -------------------------------------------------------------
    def delete_player(self, pid: str) -> None:
        """Remove the profile and all derived ratings of a player."""
        with self._lock:
            self._db["profiles"].pop(pid, None)
            self._db["notes"].pop(pid, None)
            for section in ("ladders", "history"):
                for ladder in self._db[section].values():
                    ladder.pop(pid, None)
            self._save()

    def add_erased(self, pid: str) -> None:
        with self._lock:
            if pid not in self._db["erased"]:
                self._db["erased"].append(pid)
                self._save()

    def erased_ids(self, pids) -> set[str]:
        return set(pids) & set(self._db["erased"])

    # --- merged players --------------------------------------------------------
    def get_aliases(self, pids) -> dict[str, str]:
        """{merged playerId: playerId it was merged into} for the given ids that were merged."""
        return {p: self._db["aliases"][p] for p in set(pids) if p in self._db["aliases"]}

    def set_alias(self, source: str, target: str) -> None:
        """Remember that `source` was merged into `target`; earlier merges into `source` follow."""
        with self._lock:
            aliases = self._db["aliases"]
            for k, v in aliases.items():
                if v == source:
                    aliases[k] = target
            aliases[source] = target
            self._save()

    # --- derived ratings ---------------------------------------------------
    def write_results(self, result: dict, incremental: bool = False) -> None:
        """Store engine output. Full results replace every ladder; incremental ones (from
        engine.apply_tournament) overwrite the players they contain and append their history."""
        with self._lock:
            profiles = self._db["profiles"]
            for pid, name in result["names"].items():
                prof = profiles.setdefault(pid, {"hidden": False, "membership": "", "ownerSub": ""})
                prof.update(displayName=name, spellings=result["spellings"][pid])
            if not incremental:
                self._db["ladders"], self._db["history"] = {}, {}
            for ladder, entries in result["ladders"].items():
                items = self._db["ladders"].setdefault(ladder, {})
                history = self._db["history"].setdefault(ladder, {})
                for pid, e in entries.items():
                    items[pid] = _ladder_item(pid, ladder, e, profiles[pid]["displayName"], profiles[pid])
                    history.setdefault(pid, []).extend(e["history"])
            self._save()

    def leaderboard(self, ladder: str) -> list[dict]:
        items = self._db["ladders"].get(ladder, {}).values()
        return copy.deepcopy(sorted((_leaderboard_row(i) for i in items), key=lambda r: -r["rating"]))

    def get_ladder_entry(self, pid: str, ladder: str) -> dict | None:
        return copy.deepcopy(self._db["ladders"].get(ladder, {}).get(pid))

    def get_ladder_entries(self, ladders, pids) -> dict[str, dict[str, dict]]:
        """{ladder: {playerId: entry}} for the given players that have an entry."""
        pids = set(pids)
        return {l: copy.deepcopy({p: e for p, e in self._db["ladders"].get(l, {}).items() if p in pids})
                for l in ladders}

    def get_history(self, pid: str, ladder: str) -> list[dict]:
        return copy.deepcopy(self._db["history"].get(ladder, {}).get(pid, []))

    # --- profiles ----------------------------------------------------------
    def get_profiles(self, pids) -> dict[str, dict]:
        return {pid: {"playerId": pid, **self._db["profiles"][pid]} for pid in set(pids) if pid in self._db["profiles"]}

    def update_profile(self, pid: str, **fields) -> dict | None:
        with self._lock:
            prof = self._db["profiles"].get(pid)
            if prof is None:
                return None
            prof.update({k: v for k, v in fields.items() if k in PROFILE_FIELDS})
            for ladder in self._db["ladders"].values():
                if pid in ladder:
                    ladder[pid].update({k: fields[k] for k in ("hidden", "membership") if k in fields})
            self._save()
            return {"playerId": pid, **prof}

    # --- accounts ----------------------------------------------------------
    def get_account(self, sub: str) -> dict | None:
        return copy.deepcopy(self._db["accounts"].get(sub))

    def put_account(self, account: dict) -> None:
        with self._lock:
            self._db["accounts"][account["sub"]] = copy.deepcopy(account)
            self._save()

    def list_accounts(self, claim_status: str | None = None) -> list[dict]:
        return [copy.deepcopy(a) for a in self._db["accounts"].values() if claim_status in (None, a.get("claimStatus"))]

    def delete_account(self, sub: str) -> None:
        with self._lock:
            self._db["accounts"].pop(sub, None)
            self._save()

    def delete_login(self, username: str) -> None:
        """Local development has no login directory (users are faked by local_server.py)."""

    # --- release notes -----------------------------------------------------
    def list_release_notes(self) -> list[dict]:
        return sorted((dict(r) for r in self._db["releaseNotes"].values()), key=lambda r: r["date"], reverse=True)

    def get_release_note(self, version: str) -> dict | None:
        meta = self._db["releaseNotes"].get(version)
        if not meta:
            return None
        return {**meta, "body": (self.root / meta["s3Key"]).read_text("utf-8")}

    def put_release_note(self, meta: dict, body: str) -> None:
        with self._lock:
            key = f"release-notes/{meta['version']}.md"
            (self.root / key).write_text(body, "utf-8")
            self._db["releaseNotes"][meta["version"]] = {**meta, "s3Key": key}
            self._save()


def tournament_meta(t: dict, s3_key: str, raw_key: str, old: dict | None = None) -> dict:
    """Tournament table item. PLAYER_VALUES (decks) are set by players after the
    upload and don't affect ratings, so they live only here, not in the tournament JSON; `old`
    is the previous item, whose values are kept."""
    return {
        "tournamentId": t["id"], "name": t["name"], "date": t["date"], "type": t["type"],
        "format": t.get("format"), "link": t.get("link"), "island": t.get("island"), "sourceFormat": t["sourceFormat"], "uploadedAt": t["uploadedAt"], "uploadedBy": t["uploadedBy"],
        "playerCount": len(t["players"]), "matchCount": sum(1 for m in t["matches"] if m["p2"]),
        "s3Key": s3_key, "rawKey": raw_key, **{k: (old or {}).get(k) or {} for k in PLAYER_VALUES},
    }


# ---------------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------------
def datetime_epoch(day) -> int:
    from datetime import datetime, timezone

    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


def _to_ddb(obj):
    return json.loads(json.dumps(obj), parse_float=Decimal)


def _from_ddb(obj):
    if isinstance(obj, list):
        return [_from_ddb(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _from_ddb(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    return obj


class AwsStorage:
    def __init__(self):
        import boto3  # provided by the Lambda runtime

        self.ddb = ddb = boto3.resource("dynamodb")
        self.s3 = boto3.client("s3")
        self.bucket = os.environ["DATA_BUCKET"]
        self.tournaments = ddb.Table(os.environ["TOURNAMENTS_TABLE"])
        self.players = ddb.Table(os.environ["PLAYERS_TABLE"])
        self.accounts = ddb.Table(os.environ["ACCOUNTS_TABLE"])
        self.release_notes = ddb.Table(os.environ["RELEASE_NOTES_TABLE"])

    # --- helpers -----------------------------------------------------------
    def _get_s3(self, key: str) -> str:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read().decode("utf-8")

    def _put_s3(self, key: str, body: str, content_type: str) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=body.encode("utf-8"), ContentType=content_type)

    @staticmethod
    def _scan_all(table, **kwargs) -> list[dict]:
        items, resp = [], table.scan(**kwargs)
        items += resp["Items"]
        while "LastEvaluatedKey" in resp:
            resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
            items += resp["Items"]
        return _from_ddb(items)

    @staticmethod
    def _query_all(table, **kwargs) -> list[dict]:
        items, resp = [], table.query(**kwargs)
        items += resp["Items"]
        while "LastEvaluatedKey" in resp:
            resp = table.query(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
            items += resp["Items"]
        return _from_ddb(items)

    # --- tournaments -------------------------------------------------------
    def save_tournament(self, t: dict, raw_content: str, raw_ext: str) -> None:
        s3_key, raw_key = f"tournaments/{t['id']}.json", f"raw/{t['id']}.{raw_ext}"
        self._put_s3(raw_key, raw_content, "text/plain")
        self._put_s3(s3_key, json.dumps(t), "application/json")
        old = self.tournaments.get_item(Key={"tournamentId": t["id"]}).get("Item")
        self.tournaments.put_item(Item=_to_ddb(tournament_meta(t, s3_key, raw_key, _from_ddb(old))))

    def delete_tournament(self, tid: str) -> bool:
        meta = self.tournaments.get_item(Key={"tournamentId": tid}).get("Item")
        if not meta:
            return False
        self.tournaments.delete_item(Key={"tournamentId": tid})
        for key in (meta["s3Key"], meta["rawKey"]):
            self.s3.delete_object(Bucket=self.bucket, Key=key)
        return True

    def list_tournaments(self) -> list[dict]:
        items = self._scan_all(self.tournaments)
        return sorted(items, key=lambda m: (m["date"], m["uploadedAt"]), reverse=True)

    def get_tournament(self, tid: str) -> dict | None:
        meta = self.tournaments.get_item(Key={"tournamentId": tid}).get("Item")
        return json.loads(self._get_s3(meta["s3Key"])) if meta else None

    def update_tournament(self, tid: str, **fields) -> dict | None:
        meta = self.tournaments.get_item(Key={"tournamentId": tid}).get("Item")
        if not meta:
            return None
        t = json.loads(self._get_s3(meta["s3Key"]))
        t.update({k: v for k, v in fields.items() if k in TOURNAMENT_FIELDS})
        self._put_s3(meta["s3Key"], json.dumps(t), "application/json")
        self.tournaments.put_item(Item=_to_ddb(tournament_meta(t, meta["s3Key"], meta["rawKey"], _from_ddb(meta))))
        return t

    @staticmethod
    def _set_map_entry(table, key: dict, field: str, entry: str, value, must_exist: str | None = None) -> bool:
        """Atomically set (value) or remove (None) table[key].field[entry]."""
        from botocore.exceptions import ClientError

        try:  # a nested path can only be set once the map exists
            table.update_item(Key=key, UpdateExpression="SET #f = if_not_exists(#f, :e)",
                              ExpressionAttributeNames={"#f": field}, ExpressionAttributeValues={":e": {}},
                              **({"ConditionExpression": f"attribute_exists({must_exist})"} if must_exist else {}))
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        names = {"#f": field, "#k": entry}
        if value:
            table.update_item(Key=key, UpdateExpression="SET #f.#k = :v", ExpressionAttributeNames=names,
                              ExpressionAttributeValues={":v": value})
        else:
            table.update_item(Key=key, UpdateExpression="REMOVE #f.#k", ExpressionAttributeNames=names)
        return True

    def set_player_value(self, tid: str, field: str, pid: str, value: str | None) -> bool:
        return self._set_map_entry(self.tournaments, {"tournamentId": tid}, field, pid, value, "tournamentId")

    def set_deck(self, tid: str, pid: str, deck: str | None) -> bool:
        return self.set_player_value(tid, "decks", pid, deck)

    # --- private match notes (Diamond) -------------------------------------
    def get_notes(self, pid: str) -> dict[str, dict[str, str]]:
        from boto3.dynamodb.conditions import Key

        items = self._query_all(self.players, KeyConditionExpression=Key("playerId").eq(pid) & Key("SK").begins_with("NOTES#"))
        return {i["SK"][len("NOTES#"):]: i.get("notes", {}) for i in items}

    def set_note(self, pid: str, tid: str, rnd: int, note: str | None) -> None:
        self._set_map_entry(self.players, {"playerId": pid, "SK": f"NOTES#{tid}"}, "notes", str(rnd), note)

    # --- custom profile URLs (Diamond) -------------------------------------
    def claim_slug(self, slug: str, pid: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.players.put_item(Item={"playerId": f"SLUG#{slug}", "SK": "SLUG", "owner": pid},
                                  ConditionExpression="attribute_not_exists(playerId) OR #o = :p",
                                  ExpressionAttributeNames={"#o": "owner"}, ExpressionAttributeValues={":p": pid})
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def release_slug(self, slug: str, pid: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.players.delete_item(Key={"playerId": f"SLUG#{slug}", "SK": "SLUG"}, ConditionExpression="#o = :p",
                                     ExpressionAttributeNames={"#o": "owner"}, ExpressionAttributeValues={":p": pid})
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    def resolve_slug(self, slug: str) -> str | None:
        item = self.players.get_item(Key={"playerId": f"SLUG#{slug}", "SK": "SLUG"}).get("Item")
        return item["owner"] if item else None

    # --- Eventlink events uploaded by the extension ----------------------------
    def get_eventlink(self, event_id: str) -> str | None:
        item = self.players.get_item(Key={"playerId": f"EVENTLINK#{event_id}", "SK": "EVENTLINK"}).get("Item")
        return item["tournamentId"] if item else None

    def claim_eventlink(self, event_id: str, tid: str, expected: str | None) -> bool:
        """Point an Eventlink event at tournament `tid`, only if it still points at `expected`."""
        from botocore.exceptions import ClientError

        condition = ({"ConditionExpression": "attribute_not_exists(playerId)"} if expected is None else
                     {"ConditionExpression": "tournamentId = :old", "ExpressionAttributeValues": {":old": expected}})
        try:
            self.players.put_item(Item={"playerId": f"EVENTLINK#{event_id}", "SK": "EVENTLINK", "tournamentId": tid},
                                  **condition)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    # --- background jobs (App.run_job) ---------------------------------------------
    # request and result are stored as JSON text: no float/Decimal conversions, any nesting
    @staticmethod
    def _job(item: dict | None) -> dict | None:
        if not item:
            return None
        job = {k: v for k, v in _from_ddb(item).items() if k not in ("playerId", "SK", "expiresAt")}
        for k in ("request", "result"):
            if k in job:
                job[k] = json.loads(job[k])
        return job

    def put_job(self, job_id: str, job: dict) -> None:
        """A new job: {status: "queued", request, createdAt}, deleted a day later (TTL)."""
        self.players.put_item(Item={**job, "request": json.dumps(job["request"]), "playerId": f"{JOB_PK}{job_id}",
                                    "SK": "JOB", "expiresAt": int(time.time()) + JOB_KEEP_SECONDS})

    def get_job(self, job_id: str) -> dict | None:
        return self._job(self.players.get_item(Key={"playerId": f"{JOB_PK}{job_id}", "SK": "JOB"}).get("Item"))

    def start_job(self, job_id: str, started_at: str) -> dict | None:
        """Mark a queued job running and return it; None if it is unknown or already started."""
        from botocore.exceptions import ClientError

        try:
            resp = self.players.update_item(
                Key={"playerId": f"{JOB_PK}{job_id}", "SK": "JOB"}, UpdateExpression="SET #s = :running, startedAt = :t",
                ConditionExpression="#s = :queued", ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":running": "running", ":queued": "queued", ":t": started_at},
                ReturnValues="ALL_NEW")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
        return self._job(resp["Attributes"])

    def finish_job(self, job_id: str, status_code: int, result, finished_at: str) -> None:
        self.players.update_item(
            Key={"playerId": f"{JOB_PK}{job_id}", "SK": "JOB"},
            UpdateExpression="SET #s = :done, statusCode = :code, #r = :result, finishedAt = :t",
            ExpressionAttributeNames={"#s": "status", "#r": "result"},
            ExpressionAttributeValues={":done": "done", ":code": status_code, ":result": json.dumps(result), ":t": finished_at})

    # --- upcoming tournaments ------------------------------------------------
    def list_upcoming(self) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        items = self._query_all(self.players, KeyConditionExpression=Key("playerId").eq(UPCOMING_PK))
        for i in items:
            for k in ("playerId", "SK", "expiresAt"):
                i.pop(k, None)
        return items

    def get_upcoming(self, eid: str) -> dict | None:
        item = self.players.get_item(Key={"playerId": UPCOMING_PK, "SK": f"EVENT#{eid}"}).get("Item")
        if not item:
            return None
        item = _from_ddb(item)
        for k in ("playerId", "SK", "expiresAt"):
            item.pop(k, None)
        return item

    def put_upcoming(self, event: dict) -> None:
        """expiresAt (DynamoDB TTL) removes a one-off event a month after it took place (the upload
        form offers recent ones); recurring events stay until someone removes them."""
        from datetime import date, timedelta

        item = {**event, "playerId": UPCOMING_PK, "SK": f"EVENT#{event['eventId']}"}
        if not event.get("recurrence"):
            item["expiresAt"] = datetime_epoch(date.fromisoformat(event["date"]) + timedelta(days=UPCOMING_KEEP_DAYS))
        self.players.put_item(Item=_to_ddb(item))

    def delete_upcoming(self, eid: str) -> None:
        self.players.delete_item(Key={"playerId": UPCOMING_PK, "SK": f"EVENT#{eid}"})

    # --- islands ------------------------------------------------------------
    def list_islands(self) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        items = self._query_all(self.players, KeyConditionExpression=Key("playerId").eq(ISLANDS_PK))
        islands = [{k: v for k, v in i.items() if k not in ("playerId", "SK")} for i in items]
        return sorted(islands, key=lambda i: (i["createdAt"], i["islandId"]))

    def put_island(self, island: dict) -> None:
        self.players.put_item(Item=_to_ddb({**island, "playerId": ISLANDS_PK, "SK": f"ISLAND#{island['islandId']}"}))

    # --- visitor statistics --------------------------------------------------
    def count_visitor(self, day: str, code: str) -> int:
        from datetime import date, timedelta

        expires = datetime_epoch(date.fromisoformat(day) + timedelta(days=VISITOR_KEEP_DAYS))
        resp = self.players.update_item(
            Key={"playerId": VISITOR_PK, "SK": f"{day}#{code}"},
            UpdateExpression="ADD n :one SET expiresAt = if_not_exists(expiresAt, :exp)",
            ExpressionAttributeValues={":one": 1, ":exp": expires}, ReturnValues="UPDATED_NEW")
        return int(resp["Attributes"]["n"])

    def add_page_view(self, day: str, page: str, referrer: str | None, new_visitor: bool) -> None:
        add = lambda sk, expr="ADD #v :one": self.players.update_item(  # noqa: E731
            Key={"playerId": STATS_PK, "SK": sk}, UpdateExpression=expr,
            ExpressionAttributeNames={"#v": "views", **({"#u": "visitors"} if "#u" in expr else {})},
            ExpressionAttributeValues={":one": 1})
        add(f"DAY#{day}", "ADD #v :one, #u :one" if new_visitor else "ADD #v :one")
        add(f"PAGE#{day}#{page}")
        if referrer:
            add(f"REF#{day}#{referrer}")

    def get_stats(self, first: str, last: str) -> dict:
        from boto3.dynamodb.conditions import Key

        def items(prefix: str, end: str) -> list[dict]:
            return self._query_all(self.players, KeyConditionExpression=Key("playerId").eq(STATS_PK)
                                   & Key("SK").between(f"{prefix}#{first}", f"{prefix}#{last}{end}"))

        out = {"days": {}, "pages": {}, "referrers": {}}
        for i in items("DAY", ""):
            out["days"][i["SK"][len("DAY#"):]] = {"views": i.get("views", 0), "visitors": i.get("visitors", 0)}
        for prefix, section in (("PAGE", "pages"), ("REF", "referrers")):
            for i in items(prefix, "#\uffff"):
                key = i["SK"].split("#", 2)[2]
                out[section][key] = out[section].get(key, 0) + i.get("views", 0)
        return out

    def load_all_tournaments(self) -> list[dict]:
        return [json.loads(self._get_s3(m["s3Key"])) for m in self._scan_all(self.tournaments)]

    def get_raw(self, tid: str) -> tuple[str, str] | None:
        meta = self.tournaments.get_item(Key={"tournamentId": tid}).get("Item")
        if not meta:
            return None
        return self._get_s3(meta["rawKey"]), meta["rawKey"].rsplit(".", 1)[-1]

    def _purge_versions(self, key: str, keep_latest: bool = True) -> None:
        """The data bucket is versioned: delete old versions so overwritten content is really gone."""
        for page in self.s3.get_paginator("list_object_versions").paginate(Bucket=self.bucket, Prefix=key):
            for v in page.get("Versions", []) + page.get("DeleteMarkers", []):
                if v["Key"] == key and not (keep_latest and v["IsLatest"]):
                    self.s3.delete_object(Bucket=self.bucket, Key=key, VersionId=v["VersionId"])

    def replace_tournament(self, t: dict, raw_content: str, raw_ext: str) -> None:
        old_raw = self.tournaments.get_item(Key={"tournamentId": t["id"]})["Item"]["rawKey"]
        self.save_tournament(t, raw_content, raw_ext)
        new_raw = f"raw/{t['id']}.{raw_ext}"
        for key in (f"tournaments/{t['id']}.json", new_raw):
            self._purge_versions(key)
        if old_raw != new_raw:
            self._purge_versions(old_raw, keep_latest=False)

    # --- erasure -------------------------------------------------------------
    def delete_player(self, pid: str) -> None:
        from boto3.dynamodb.conditions import Key

        items = self._query_all(self.players, KeyConditionExpression=Key("playerId").eq(pid),
                                ProjectionExpression="playerId, SK")
        with self.players.batch_writer() as batch:
            for i in items:
                if i["SK"] not in ("ERASED", "ALIAS"):
                    batch.delete_item(Key={"playerId": pid, "SK": i["SK"]})

    def add_erased(self, pid: str) -> None:
        self.players.put_item(Item={"playerId": pid, "SK": "ERASED"})

    def erased_ids(self, pids) -> set[str]:
        return {i["playerId"] for i in self._batch_get([{"playerId": p, "SK": "ERASED"} for p in set(pids)])}

    # --- merged players --------------------------------------------------------
    def get_aliases(self, pids) -> dict[str, str]:
        return {i["playerId"]: i["target"] for i in self._batch_get([{"playerId": p, "SK": "ALIAS"} for p in set(pids)])}

    def set_alias(self, source: str, target: str) -> None:
        from boto3.dynamodb.conditions import Attr

        # merges are rare: a scan for the aliases that pointed at `source` is fine
        for i in self._scan_all(self.players, FilterExpression=Attr("SK").eq("ALIAS") & Attr("target").eq(source)):
            self.players.put_item(Item={"playerId": i["playerId"], "SK": "ALIAS", "target": target})
        self.players.put_item(Item={"playerId": source, "SK": "ALIAS", "target": target})

    # --- derived ratings ---------------------------------------------------
    def write_results(self, result: dict, incremental: bool = False) -> None:
        """See LocalStorage.write_results. Incremental writes touch only the players in `result`."""
        if incremental:
            profiles, stale = self.get_profiles(result["names"]), set()
        else:
            existing = self._scan_all(
                self.players,
                ProjectionExpression="playerId, SK, #h, #m",
                ExpressionAttributeNames={"#h": "hidden", "#m": "membership"},
            )
            profiles = {i["playerId"]: i for i in existing if i["SK"] == "PROFILE"}
            stale = {(i["playerId"], i["SK"]) for i in existing if i["SK"].startswith(("LADDER#", "HIST#"))}

        for pid, name in result["names"].items():
            self.players.update_item(
                Key={"playerId": pid, "SK": "PROFILE"},
                UpdateExpression="SET displayName = :n, spellings = :s, "
                                 "#h = if_not_exists(#h, :f), #m = if_not_exists(#m, :e)",
                ExpressionAttributeNames={"#h": "hidden", "#m": "membership"},
                ExpressionAttributeValues={":n": name, ":s": _to_ddb(result["spellings"][pid]), ":f": False, ":e": ""},
            )

        with self.players.batch_writer(overwrite_by_pkeys=["playerId", "SK"]) as batch:
            for ladder, entries in result["ladders"].items():
                for pid, entry in entries.items():
                    item = _ladder_item(pid, ladder, entry, result["names"][pid], profiles.get(pid, {}))
                    item["SK"] = f"LADDER#{ladder}"
                    batch.put_item(Item=_to_ddb(item))
                    stale.discard((pid, item["SK"]))
                    for h in entry["history"]:
                        sk = f"HIST#{ladder}#{h['seq']:06d}"
                        batch.put_item(Item=_to_ddb({"playerId": pid, "SK": sk, **h}))
                        stale.discard((pid, sk))
            for pid, sk in stale:
                batch.delete_item(Key={"playerId": pid, "SK": sk})

    def leaderboard(self, ladder: str) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        items = self._query_all(self.players, IndexName="leaderboard",
                                KeyConditionExpression=Key("ladder").eq(ladder), ScanIndexForward=False)
        return [_leaderboard_row(i) for i in items]

    def get_ladder_entry(self, pid: str, ladder: str) -> dict | None:
        item = self.players.get_item(Key={"playerId": pid, "SK": f"LADDER#{ladder}"}).get("Item")
        return _from_ddb(item) if item else None

    def get_ladder_entries(self, ladders, pids) -> dict[str, dict[str, dict]]:
        out = {l: {} for l in ladders}
        for item in self._batch_get([{"playerId": p, "SK": f"LADDER#{l}"} for l in out for p in set(pids)]):
            out[item["ladder"]][item["playerId"]] = item
        return out

    def get_history(self, pid: str, ladder: str) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        items = self._query_all(
            self.players,
            KeyConditionExpression=Key("playerId").eq(pid) & Key("SK").begins_with(f"HIST#{ladder}#"),
        )
        for i in items:
            i.pop("SK", None)
            i.pop("playerId", None)
        return items

    # --- profiles ----------------------------------------------------------
    def _batch_get(self, keys: list[dict]) -> list[dict]:
        items = []
        for i in range(0, len(keys), 100):
            request = {self.players.name: {"Keys": keys[i:i + 100]}}
            while request:
                resp = self.ddb.batch_get_item(RequestItems=request)
                items += _from_ddb(resp["Responses"].get(self.players.name, []))
                request = resp.get("UnprocessedKeys") or None
        return items

    def get_profiles(self, pids) -> dict[str, dict]:
        out = {}
        for item in self._batch_get([{"playerId": pid, "SK": "PROFILE"} for pid in set(pids)]):
            item.pop("SK", None)
            out[item["playerId"]] = item
        return out

    def update_profile(self, pid: str, **fields) -> dict | None:
        from botocore.exceptions import ClientError

        fields = {k: v for k, v in fields.items() if k in PROFILE_FIELDS}
        if not fields:
            return self.get_profiles([pid]).get(pid)
        names = {f"#{k}": k for k in fields}
        values = {f":{k}": v for k, v in fields.items()}
        expr = "SET " + ", ".join(f"#{k} = :{k}" for k in fields)
        try:
            resp = self.players.update_item(
                Key={"playerId": pid, "SK": "PROFILE"}, UpdateExpression=expr,
                ExpressionAttributeNames=names, ExpressionAttributeValues=values,
                ConditionExpression="attribute_exists(playerId)", ReturnValues="ALL_NEW",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
        mirror = {k: v for k, v in fields.items() if k in ("hidden", "membership")}
        if mirror:
            from boto3.dynamodb.conditions import Key

            # every ladder the player is on, in every island
            entries = self._query_all(self.players, ProjectionExpression="SK",
                                      KeyConditionExpression=Key("playerId").eq(pid) & Key("SK").begins_with("LADDER#"))
            for e in entries:
                self.players.update_item(
                    Key={"playerId": pid, "SK": e["SK"]},
                    UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in mirror),
                    ExpressionAttributeNames={f"#{k}": k for k in mirror},
                    ExpressionAttributeValues={f":{k}": v for k, v in mirror.items()},
                )
        item = _from_ddb(resp["Attributes"])
        item.pop("SK", None)
        return item

    # --- accounts ----------------------------------------------------------
    def get_account(self, sub: str) -> dict | None:
        item = self.accounts.get_item(Key={"sub": sub}).get("Item")
        return _from_ddb(item) if item else None

    def put_account(self, account: dict) -> None:
        self.accounts.put_item(Item=_to_ddb(account))

    def list_accounts(self, claim_status: str | None = None) -> list[dict]:
        items = self._scan_all(self.accounts)
        return [a for a in items if claim_status in (None, a.get("claimStatus"))]

    def delete_account(self, sub: str) -> None:
        self.accounts.delete_item(Key={"sub": sub})

    def delete_login(self, username: str) -> None:
        """Delete the Cognito user (USER_POOL_ID is set on the deployed Lambda)."""
        pool = os.environ.get("USER_POOL_ID")
        if not pool:
            return
        import boto3

        cognito = boto3.client("cognito-idp")
        try:
            cognito.admin_delete_user(UserPoolId=pool, Username=username)
        except cognito.exceptions.UserNotFoundException:
            pass

    # --- release notes -----------------------------------------------------
    def list_release_notes(self) -> list[dict]:
        return sorted(self._scan_all(self.release_notes), key=lambda r: r["date"], reverse=True)

    def get_release_note(self, version: str) -> dict | None:
        meta = self.release_notes.get_item(Key={"version": version}).get("Item")
        if not meta:
            return None
        return {**_from_ddb(meta), "body": self._get_s3(meta["s3Key"])}

    def put_release_note(self, meta: dict, body: str) -> None:
        key = f"release-notes/{meta['version']}.md"
        self._put_s3(key, body, "text/markdown")
        self.release_notes.put_item(Item=_to_ddb({**meta, "s3Key": key}))
