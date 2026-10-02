"""Import Eventlink pastes (raw-data-eventlink/*.txt) into Delo storage and recalculate Elo once.

    py backend/import_events.py --target aws [--function delo-prod-api] [--profile NAME]
    py backend/import_events.py --target local [--data .localdata]

Options: --source DIR (default raw-data-eventlink/), --dry-run (parse and list only),
--replace (delete every stored tournament first, e.g. after fixing dates in events.json).

Event names, dates, types (rel/casual) and optional links come from events.json in the source
folder. The first run writes it with guesses from the file names; fix the entries marked "todo" and
run again. All events are Duel Commander unless an entry sets another "format" (see delo/elo.py).
Files with several events pasted together are split into "file.txt#1", "file.txt#2", ...
Tournaments that are already stored (same content and metadata) are skipped; if only their format
differs, the stored format is updated.

--target aws needs boto3 and AWS credentials. Table and bucket names are read from the deployed
Lambda's environment (`terraform output lambda_function`).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from delo.api import App  # noqa: E402
from delo.parsers import ParseError, build_tournament, split_eventlink  # noqa: E402

RAW_DATA = ROOT / "raw-data-eventlink"
MANIFEST = "events.json"
DEFAULT_FORMAT = "duel-commander"  # every event in raw-data-eventlink/ is Duel Commander


def guess_date(stem: str, year: int) -> str | None:
    """Day and month from names like '29.1. DC', 'Najada_10.2', 'gdc25_04', 'najada1606', 'dc176'."""
    candidates = []
    if m := re.search(r"(?<!\d)(\d{1,2})[._](\d{1,2})(?!\d)", stem):
        candidates.append((m.group(1), m.group(2)))
    elif m := re.search(r"(?<!\d)(\d\d)(\d\d)(?!\d)", stem):
        candidates.append((m.group(1), m.group(2)))
    elif m := re.search(r"(?<!\d)(\d)(\d)(\d)(?!\d)", stem):
        candidates += [(m.group(1) + m.group(2), m.group(3)), (m.group(1), m.group(2) + m.group(3))]
    for day, month in candidates:
        try:
            return date(year, int(month), int(day)).isoformat()
        except ValueError:
            continue
    return None


def read_events(source: Path) -> list[tuple[str, str]]:
    """(key, content) per event; files with several events pasted together get 'file#1', 'file#2', ..."""
    events = []
    for path in sorted(source.glob("*.txt")):
        parts = split_eventlink(path.read_text("utf-8-sig"))
        for n, content in enumerate(parts, start=1):
            events.append((path.name if len(parts) == 1 else f"{path.name}#{n}", content))
    return events


def build_manifest(events: list[tuple[str, str]], year: int) -> dict:
    manifest = {}
    for key, _ in events:
        filename, _, part = key.partition("#")
        stem = Path(filename).stem.strip()
        guessed = guess_date(stem, year)
        entry = {"name": f"{stem} ({part})" if part else stem, "date": guessed or f"{year}-01-01", "type": "rel",
                 "format": DEFAULT_FORMAT, "link": ""}
        if not guessed:
            entry["todo"] = "date not found in the file name, set it and delete this line"
        manifest[key] = entry
    return manifest


def load_manifest(source: Path, events: list[tuple[str, str]]) -> dict:
    path = source / MANIFEST
    if not path.exists():
        path.write_text(json.dumps(build_manifest(events, date.today().year), indent=2, ensure_ascii=False), "utf-8")
        print(f"wrote {path}: check the guessed names, dates and types there")
    return json.loads(path.read_text("utf-8-sig"))


def import_events(storage, source: Path, uploaded_by: str = "import", dry_run: bool = False,
                  replace: bool = False) -> None:
    events = read_events(source)
    manifest = load_manifest(source, events)
    if replace and not dry_run:
        for t in storage.list_tournaments():
            storage.delete_tournament(t["tournamentId"])
        print("deleted all stored tournaments")
    existing = {t["tournamentId"]: t.get("format") for t in storage.list_tournaments()}
    imported = updated = failed = 0
    for key, content in events:
        meta = manifest.get(key)
        if meta is None or meta.get("skip"):
            print(f"{key}: skipped ({'not in ' + MANIFEST if meta is None else 'skip'})")
            continue
        try:
            t = build_tournament(content=content, name=meta.get("name", ""), date=meta.get("date", ""),
                                 type=meta.get("type", ""), fmt="eventlink", game_format=meta.get("format") or DEFAULT_FORMAT,
                                 link=meta.get("link", ""), uploaded_by=uploaded_by,
                                 uploaded_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        except ParseError as err:
            print(f"{key}: ERROR {err}")
            failed += 1
            continue
        todo = f"  (todo: {meta['todo']})" if "todo" in meta else ""
        if t["id"] in existing:
            if existing[t["id"]] == t["format"]:
                print(f"{key}: already stored as {t['id']}")
                continue
            print(f"{key}: already stored as {t['id']}, format {existing[t['id']] or '(none)'} -> {t['format']}")
            if not dry_run:
                storage.update_tournament(t["id"], format=t["format"])
                existing[t["id"]] = t["format"]
            updated += 1
            continue
        print(f"{key}: {t['date']} {t['type']} {t['format'] or '(no format)'} '{t['name']}', "
              f"{len(t['players'])} players, {len(t['matches'])} matches{todo}")
        if not dry_run:
            storage.save_tournament(t, content, "txt")
            existing[t["id"]] = t["format"]
        imported += 1
    if dry_run:
        print(f"dry run: {imported} events would be imported, {updated} updated, {failed} failed")
        return
    result = App(storage).recalculate()
    print(f"imported {imported} events, updated {updated}, {failed} failed; recalculated {result['tournaments']} tournaments, "
          f"{result['players']} players")


def aws_storage(function: str, profile: str | None, region: str | None):
    import boto3

    session = boto3.Session(profile_name=profile, region_name=region)
    env = session.client("lambda").get_function_configuration(FunctionName=function)["Environment"]["Variables"]
    for name in ("DATA_BUCKET", "TOURNAMENTS_TABLE", "PLAYERS_TABLE", "ACCOUNTS_TABLE", "RELEASE_NOTES_TABLE"):
        os.environ[name] = env[name]
    if profile:
        os.environ["AWS_PROFILE"] = profile
    os.environ.setdefault("AWS_DEFAULT_REGION", session.region_name)
    from delo.storage import AwsStorage

    return AwsStorage()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Eventlink pastes into Delo storage.")
    parser.add_argument("--target", choices=("aws", "local"), required=True)
    parser.add_argument("--source", default=str(RAW_DATA), help="folder of Eventlink .txt pastes")
    parser.add_argument("--data", default=str(ROOT / ".localdata"), help="local storage folder (--target local)")
    parser.add_argument("--function", default="delo-prod-api", help="deployed Lambda name (--target aws)")
    parser.add_argument("--profile", help="AWS profile (--target aws)")
    parser.add_argument("--region", help="AWS region (--target aws)")
    parser.add_argument("--dry-run", action="store_true", help="parse and list, store nothing")
    parser.add_argument("--replace", action="store_true", help="delete all stored tournaments before importing")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.is_dir():
        sys.exit(f"{source} does not exist")
    if args.target == "aws":
        storage = aws_storage(args.function, args.profile, args.region)
    else:
        from delo.storage import LocalStorage

        storage = LocalStorage(args.data)
    import_events(storage, source, dry_run=args.dry_run, replace=args.replace)


if __name__ == "__main__":
    main()
