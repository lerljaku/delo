"""Re-read stored Eventlink tournaments with the current parser, then recalculate every rating.

    py backend/reparse_eventlink.py --target aws [--function mtgdelo-prod-api] [--profile NAME] [--dry-run]
    py backend/reparse_eventlink.py --target local [--data .localdata] [--dry-run]

For when the parser changes what a paste means, e.g. 3-digit game scores: Eventlink puts the
drawn games in the middle (030 = no wins, 3 draws: an intentional draw), which used to be read
as a 0-3 loss. Every tournament uploaded as Eventlink pairings is parsed again from its stored
raw text. Those whose matches come out different are replaced, the way an admin's "upload
corrected results" does: same name, date, type, format, link, island, uploader and upload time,
players' decks carried over. Their id changes (it is a hash of the matches). Running it again
changes nothing.

Skipped and listed: tournaments with an erased player (their raw text has "Deleted player"
instead of the names, which would merge them into one player) and raw text that is no longer
Eventlink pairings.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from delo.api import App, _eventlink_event  # noqa: E402
from delo.parsers import ParseError  # noqa: E402
from delo.privacy import ERASED_NAME  # noqa: E402
from delo.storage import PLAYER_VALUES  # noqa: E402


def _match_key(t: dict) -> list:
    return [(m["round"], m["p1"], m["p2"], m["p1Wins"], m["p2Wins"], m["draws"]) for m in t["matches"]]


def reparse(storage, dry_run: bool = False) -> dict:
    app = App(storage)
    replaced, skipped = [], []
    metas = [m for m in storage.list_tournaments() if m.get("sourceFormat") == "eventlink"]
    stored_ids = {m["tournamentId"] for m in storage.list_tournaments()}
    for meta in metas:
        tid = meta["tournamentId"]
        old = storage.get_tournament(tid)
        raw = storage.get_raw(tid)
        if ERASED_NAME in old["players"].values():
            skipped.append(f"{tid}: has an erased player, check it by hand")
            continue
        if not raw or raw[1] != "txt":
            skipped.append(f"{tid}: raw text isn't Eventlink pairings any more")
            continue
        body = {"content": raw[0], "name": old["name"], "date": old["date"], "type": old["type"],
                "format": old.get("format") or "", "link": old.get("link") or "", "sourceFormat": "eventlink"}
        try:
            t, content, ext, *_ = app._prepare_upload({}, body, old.get("uploadedBy") or "")
        except ParseError as err:
            skipped.append(f"{tid}: {err}")
            continue
        if _match_key(t) == _match_key(old):
            continue
        if t["id"] in stored_ids:
            skipped.append(f"{tid}: the corrected results are already stored as {t['id']}")
            continue
        t["island"], t["uploadedAt"] = old.get("island"), old.get("uploadedAt") or t["uploadedAt"]
        changed = sum(a != b for a, b in zip(_match_key(t), _match_key(old))) + abs(len(t["matches"]) - len(old["matches"]))
        print(f"{tid} -> {t['id']}: '{old['name']}' {old['date']}, {changed} match(es) change")
        replaced.append((tid, t["id"]))
        if dry_run:
            continue
        storage.save_tournament(t, content, ext)
        for field in PLAYER_VALUES:
            for pid, value in (meta.get(field) or {}).items():
                if pid in t["players"]:
                    storage.set_player_value(t["id"], field, pid, value)
        # the Chrome extension's record of which tournament holds this Eventlink event
        event = _eventlink_event(old.get("link"))
        if event and storage.get_eventlink(event) == tid:
            storage.claim_eventlink(event, t["id"], tid)
        storage.delete_tournament(tid)
        stored_ids |= {t["id"]}
    for line in skipped:
        print(f"skipped {line}")
    print(f"{len(metas)} Eventlink tournaments checked, {len(replaced)} {'would be ' if dry_run else ''}replaced, {len(skipped)} skipped")
    result = {"replaced": replaced, "skipped": skipped}
    if replaced and not dry_run:
        rc = app.recalculate()
        print(f"recalculated {rc['tournaments']} tournaments, {rc['players']} players")
        result["recalculation"] = rc
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", choices=("aws", "local"), required=True)
    parser.add_argument("--data", default=str(ROOT / ".localdata"), help="local storage folder (--target local)")
    parser.add_argument("--function", default="mtgdelo-prod-api", help="deployed Lambda name (--target aws)")
    parser.add_argument("--profile", help="AWS profile (--target aws)")
    parser.add_argument("--region", help="AWS region (--target aws)")
    parser.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    args = parser.parse_args()
    if args.target == "aws":
        from import_events import aws_storage

        storage = aws_storage(args.function, args.profile, args.region)
    else:
        from delo.storage import LocalStorage

        storage = LocalStorage(args.data)
    reparse(storage, args.dry_run)


if __name__ == "__main__":
    main()
