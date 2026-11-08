"""Move all data that has no island yet into one island, then recalculate every rating.

    py backend/migrate_islands.py --target aws [--function mtgdelo-prod-api] [--profile NAME] [--dry-run]
    py backend/migrate_islands.py --target local [--data .localdata] [--dry-run]

Options: --name "Czech Island" (default), --icon 🇨🇿 (default). The island is created if it
doesn't exist (its id is the name as a slug: czech-island). Every tournament and upcoming tournament without an island is
assigned to it; then all ratings are recalculated, so the leaderboards move to the island's
ladders (`czech-island/rel`, ...). Running it again changes nothing that is already migrated.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from delo.api import App  # noqa: E402
from delo.parsers import _slug  # noqa: E402


def migrate(storage, name: str = "Czech Island", dry_run: bool = False, icon: str = "🇨🇿") -> dict:
    iid = _slug(name)
    island = next((i for i in storage.list_islands() if i["islandId"] == iid), None)
    if island:
        print(f"island '{island['name']}' ({iid}) exists")
    else:
        print(f"{'would create' if dry_run else 'creating'} island '{name}' ({iid})")
        if not dry_run:
            storage.put_island({"islandId": iid, "name": name, "icon": icon,
                                "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})

    tournaments = [m for m in storage.list_tournaments() if not m.get("island")]
    events = [e for e in storage.list_upcoming() if not e.get("island")]
    print(f"{len(tournaments)} tournaments and {len(events)} upcoming tournaments without an island")
    if dry_run:
        print("dry run: nothing changed")
        return {"island": iid, "tournaments": len(tournaments), "upcoming": len(events), "recalculation": None}

    for m in tournaments:
        storage.update_tournament(m["tournamentId"], island=iid)
    for e in events:
        storage.put_upcoming({**e, "island": iid})
    result = App(storage).recalculate()
    print(f"moved {len(tournaments)} tournaments and {len(events)} upcoming tournaments to '{iid}'; "
          f"recalculated {result['tournaments']} tournaments, {result['players']} players")
    return {"island": iid, "tournaments": len(tournaments), "upcoming": len(events), "recalculation": result}


def main() -> None:
    parser = argparse.ArgumentParser(description="Move data without an island into one island and recalculate.")
    parser.add_argument("--target", choices=("aws", "local"), required=True)
    parser.add_argument("--name", default="Czech Island", help="island to create or use")
    parser.add_argument("--icon", default="🇨🇿", help="icon of a newly created island (emoji)")
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
    migrate(storage, args.name, args.dry_run, args.icon)


if __name__ == "__main__":
    main()
