"""Persistence. LocalStorage (JSON files, for development/tests) and AwsStorage
(S3 + DynamoDB) implement the same interface; see ARCHITECTURE.md §3 for the layout.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from decimal import Decimal
from pathlib import Path

from .elo import LADDERS

PROFILE_FIELDS = ("displayName", "hidden", "membership", "ownerSub")
SUMMARY_FIELDS = ("matches", "wins", "losses", "draws", "winrate", "peakRating")


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

    def _save(self) -> None:
        tmp = self._db_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._db, indent=1), "utf-8")
        tmp.replace(self._db_path)

    # --- tournaments -------------------------------------------------------
    def save_tournament(self, t: dict, raw_content: str, raw_ext: str) -> None:
        with self._lock:
            (self.root / "raw" / f"{t['id']}.{raw_ext}").write_text(raw_content, "utf-8")
            (self.root / "tournaments" / f"{t['id']}.json").write_text(json.dumps(t), "utf-8")
            self._db["tournaments"][t["id"]] = tournament_meta(t, f"tournaments/{t['id']}.json",
                                                               f"raw/{t['id']}.{raw_ext}")
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

    def load_all_tournaments(self) -> list[dict]:
        return [self.get_tournament(tid) for tid in list(self._db["tournaments"])]

    # --- derived ratings ---------------------------------------------------
    def write_results(self, result: dict) -> None:
        with self._lock:
            profiles = self._db["profiles"]
            for pid, name in result["names"].items():
                profiles.setdefault(pid, {"hidden": False, "membership": "", "ownerSub": ""})["displayName"] = name
            self._db["ladders"] = {
                ladder: {pid: _ladder_item(pid, ladder, e, result["names"][pid], profiles[pid])
                         for pid, e in entries.items()}
                for ladder, entries in result["ladders"].items()
            }
            self._db["history"] = {
                ladder: {pid: e["history"] for pid, e in entries.items()}
                for ladder, entries in result["ladders"].items()
            }
            self._save()

    def leaderboard(self, ladder: str) -> list[dict]:
        items = self._db["ladders"].get(ladder, {}).values()
        return copy.deepcopy(sorted((_leaderboard_row(i) for i in items), key=lambda r: -r["rating"]))

    def get_ladder_entry(self, pid: str, ladder: str) -> dict | None:
        return copy.deepcopy(self._db["ladders"].get(ladder, {}).get(pid))

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


def tournament_meta(t: dict, s3_key: str, raw_key: str) -> dict:
    return {
        "tournamentId": t["id"], "name": t["name"], "date": t["date"], "type": t["type"],
        "sourceFormat": t["sourceFormat"], "uploadedAt": t["uploadedAt"], "uploadedBy": t["uploadedBy"],
        "playerCount": len(t["players"]), "matchCount": sum(1 for m in t["matches"] if m["p2"]),
        "s3Key": s3_key, "rawKey": raw_key,
    }


# ---------------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------------
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
        self.tournaments.put_item(Item=_to_ddb(tournament_meta(t, s3_key, raw_key)))

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

    def load_all_tournaments(self) -> list[dict]:
        return [json.loads(self._get_s3(m["s3Key"])) for m in self._scan_all(self.tournaments)]

    # --- derived ratings ---------------------------------------------------
    def write_results(self, result: dict) -> None:
        existing = self._scan_all(
            self.players,
            ProjectionExpression="playerId, SK, #h, #m",
            ExpressionAttributeNames={"#h": "hidden", "#m": "membership"},
        )
        profiles = {i["playerId"]: i for i in existing if i["SK"] == "PROFILE"}
        stale = {(i["playerId"], i["SK"]) for i in existing if i["SK"] != "PROFILE"}

        for pid, name in result["names"].items():
            self.players.update_item(
                Key={"playerId": pid, "SK": "PROFILE"},
                UpdateExpression="SET displayName = :n, #h = if_not_exists(#h, :f), #m = if_not_exists(#m, :e)",
                ExpressionAttributeNames={"#h": "hidden", "#m": "membership"},
                ExpressionAttributeValues={":n": name, ":f": False, ":e": ""},
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
    def get_profiles(self, pids) -> dict[str, dict]:
        keys = [{"playerId": pid, "SK": "PROFILE"} for pid in set(pids)]
        out = {}
        for i in range(0, len(keys), 100):
            request = {self.players.name: {"Keys": keys[i:i + 100]}}
            while request:
                resp = self.ddb.batch_get_item(RequestItems=request)
                for item in _from_ddb(resp["Responses"].get(self.players.name, [])):
                    item.pop("SK", None)
                    out[item["playerId"]] = item
                request = resp.get("UnprocessedKeys") or None
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
            for ladder in LADDERS:
                try:
                    self.players.update_item(
                        Key={"playerId": pid, "SK": f"LADDER#{ladder}"},
                        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in mirror),
                        ExpressionAttributeNames={f"#{k}": k for k in mirror},
                        ExpressionAttributeValues={f":{k}": v for k, v in mirror.items()},
                        ConditionExpression="attribute_exists(playerId)",
                    )
                except ClientError as e:
                    if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                        raise
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
