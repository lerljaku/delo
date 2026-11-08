"""Turn a melee.gg tournament into an Eventlink paste (raw-data-eventlink/*.txt) that Delo can import.

    py backend/melee_to_eventlink.py https://melee.gg/Tournament/View/480380 [--type casual]

Options: --out FILE (default raw-data-eventlink/melee <id> <name> <d.m>.txt), --names FILE (melee
username -> Eventlink name, default raw-data-eventlink/melee-names.json), --format (default
duel-commander), --type rel|casual (default rel), --no-manifest (don't add the event to events.json),
--show (run the browser visibly, for debugging).

melee.gg blocks plain HTTP clients, so this opens the page in a real (headless) Edge or Chrome via
Playwright and, from inside it, reads every round's matches the way its pairings table does. Needs `pip install playwright`;
no `playwright install` is needed, it uses the browser already on the machine.

Usernames missing from the names file are written as they are on melee.gg and listed at the end, so
they can be added to the file (then run again) or merged on the Admin page after uploading.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from delo.parsers import ParseError, build_tournament  # noqa: E402
from import_events import DEFAULT_FORMAT, MANIFEST, RAW_DATA  # noqa: E402

NAMES = RAW_DATA / "melee-names.json"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0")
DASH = "–"  # Eventlink records use en dashes: 2–1–0

# Round ids and event details from the page; then each round's matches from the endpoint the page's
# pairings table loads them from (a DataTables request), fetched inside the page so it passes the bot check.
JS_INFO = """() => ({
  title: document.title,
  text: document.body.innerText,
  rounds: [...document.querySelectorAll("#pairings-round-selector-container button.round-selector")]
    .map((b) => ({ id: b.dataset.id, name: b.dataset.name || b.innerText.trim() })),
})"""
JS_MATCHES = """async (roundId) => {
  const body = new URLSearchParams({ draw: "1", start: "0", length: "-1", "search[value]": "", "search[regex]": "false",
    "order[0][column]": "0", "order[0][dir]": "asc" });
  ["TableNumber", "PodNumber", "Teams", "Decklists", "ResultString"].forEach((c, i) => {
    body.set(`columns[${i}][data]`, c); body.set(`columns[${i}][name]`, c);
    body.set(`columns[${i}][searchable]`, "true"); body.set(`columns[${i}][orderable]`, "true");
    body.set(`columns[${i}][search][value]`, ""); body.set(`columns[${i}][search][regex]`, "false");
  });
  const r = await fetch(`/Match/GetRoundMatches/${roundId}`, { method: "POST", body,
    headers: { "X-Requested-With": "XMLHttpRequest" } });
  if (!r.ok) throw new Error(`round ${roundId}: HTTP ${r.status}`);
  return (await r.json()).data;
}"""


def scrape(url: str, show: bool = False) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("needs Playwright: pip install playwright")
    with sync_playwright() as pw:
        browser = None
        for channel in ("msedge", "chrome"):
            try:
                browser = pw.chromium.launch(channel=channel, headless=not show)
                break
            except Exception:  # noqa: BLE001 - that browser isn't installed, try the next one
                continue
        if browser is None:
            sys.exit("neither Microsoft Edge nor Google Chrome found")
        try:
            page = browser.new_context(user_agent=USER_AGENT, locale="en-US").new_page()
            page.goto(url, wait_until="networkidle", timeout=60_000)
            page.wait_for_selector("#pairings-round-selector-container button.round-selector", timeout=30_000)
            info = page.evaluate(JS_INFO)
            rounds = [{**r, "matches": page.evaluate(JS_MATCHES, r["id"])} for r in info["rounds"]]
            return {**info, "rounds": rounds}
        finally:
            browser.close()


def player_name(competitor: dict) -> str:
    return " / ".join(p["DisplayName"].strip() for p in competitor["Team"]["Players"])


def to_eventlink(data: dict, names: dict[str, str]) -> tuple[str, set[str], list[str]]:
    """The Eventlink paste, the melee names missing from `names` and the matches skipped for having no result."""
    folded = {k.casefold(): v for k, v in names.items()}
    unknown: set[str] = set()
    skipped: list[str] = []

    def name(user: str) -> str:
        if user.casefold() in folded:
            return names.get(user) or folded[user.casefold()]
        unknown.add(user)
        return user

    records: dict[str, list[int]] = {}

    def record(player: str, w: int, l: int) -> str:
        rec = records.setdefault(player, [0, 0, 0])
        rec[0 if w > l else 1 if l > w else 2] += 1
        return DASH.join(map(str, rec))

    lines = []
    for r in data["rounds"]:
        for m in r["matches"]:
            comps = sorted(m["Competitors"], key=lambda c: c.get("SortOrder") or 0)
            if len(comps) == 1:  # bye
                a = name(player_name(comps[0]))
                lines += [a, record(a, 2, 0), "20", "Bye"]
                continue
            if len(comps) != 2:
                raise ValueError(f"{r['name']}: a match with {len(comps)} players, only 1-vs-1 is supported")
            a, b = (name(player_name(c)) for c in comps)
            if not m.get("HasResult"):
                skipped.append(f"{r['name']}: {a} vs {b}")
                continue
            w1, w2, d = (comps[0].get("GameWins") or 0), (comps[1].get("GameWins") or 0), (m.get("GameDraws") or 0)
            if m.get("TableNumber") is not None:
                lines.append(str(m["TableNumber"]))
            # as Eventlink shows scores: A's wins, drawn games (only if any), B's wins
            lines += [a, record(a, w1, w2), f"{w1}{d}{w2}" if d else f"{w1}{w2}", b, record(b, w2, w1)]
    return "\n".join(lines) + "\n", unknown, skipped


def event_details(data: dict) -> tuple[str, str | None]:
    """Name (page title without the site suffix) and date (melee shows US dates: 10/07/2026)."""
    title = re.split(r"\s+[|\-–]\s+Melee", data["title"], maxsplit=1)[0].strip() or "melee event"
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", data["text"])
    return title, date(int(m[3]), int(m[1]), int(m[2])).isoformat() if m else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url", help="melee.gg tournament link, e.g. https://melee.gg/Tournament/View/480380")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--names", type=Path, default=NAMES)
    ap.add_argument("--format", default=DEFAULT_FORMAT)
    ap.add_argument("--type", choices=("rel", "casual"), default="rel")
    ap.add_argument("--no-manifest", action="store_true")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    m = re.search(r"melee\.gg/Tournament/View/(\d+)", args.url, re.I)
    if not m:
        sys.exit("expected a link like https://melee.gg/Tournament/View/480380")
    tid, url = m[1], f"https://melee.gg/Tournament/View/{m[1]}"
    names = json.loads(args.names.read_text("utf-8-sig")) if args.names.exists() else {}

    data = scrape(url, args.show)
    if not any(r["matches"] for r in data["rounds"]):
        sys.exit("no pairings found (has the tournament started? did melee.gg change its page?)")
    content, unknown, skipped = to_eventlink(data, names)
    name, day = event_details(data)

    try:  # check that Delo reads it back
        t = build_tournament(content=content, name=name, date=day or "2000-01-01", type=args.type, fmt="eventlink",
                             game_format=args.format, link=url, uploaded_by="melee", uploaded_at="")
    except ParseError as err:
        sys.exit(f"Delo can't read the result: {err}")

    safe = re.sub(r'[<>:"/\\|?*]', "", name).strip()
    out = args.out or RAW_DATA / (f"melee {tid} {safe}" + (f" {int(day[8:])}.{int(day[5:7])}" if day else "") + ".txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, "utf-8")
    print(f"wrote {out}: {name}, {day or 'date not found'}, {len(data['rounds'])} rounds, "
          f"{len(t['players'])} players, {len(t['matches'])} matches")

    manifest_path = out.parent / MANIFEST
    if not args.no_manifest and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text("utf-8-sig"))
        if out.name not in manifest:
            entry = {"name": name, "date": day or f"{date.today().year}-01-01", "type": args.type,
                     "format": args.format, "link": url}
            if not day:
                entry["todo"] = "date not found on melee.gg, set it and delete this line"
            manifest[out.name] = entry
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", "utf-8")
            print(f"added it to {manifest_path} (check name, date and type there)")
    if skipped:
        print(f"\n{len(skipped)} matches have no result yet and were left out:")
        for s in skipped:
            print(f"  {s}")
    if unknown:
        print(f"\n{len(unknown)} melee usernames not in {args.names.name}, written as they are:")
        for u in sorted(unknown, key=str.casefold):
            print(f"  {u}")


if __name__ == "__main__":
    main()
