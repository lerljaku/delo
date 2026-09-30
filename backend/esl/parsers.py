"""Parse uploaded tournament results into the normalized tournament structure.

Supported formats (documented for users on frontend/docs.html#formats):
  csv  - header row with round, player1, player2, player1_wins, player2_wins[, draws]
         (or a single "result" column like "2-1-0")
  json - {"rounds": [{"round": 1, "matches": [...]}]} or {"matches": [...]}
  text - pasted pairings: "Round 1" headers and lines "Alice vs Bob 2-1-0" / "Carol - BYE"
  eventlink - pairings copied from Eventlink/Companion: blocks of
         table, player A, A's record "W–L–D", game score "21", player B, B's record
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from datetime import date as date_cls

from .elo import TOURNAMENT_TYPES

FORMATS = ("csv", "json", "text", "eventlink")
BYE_NAMES = {"bye", "-", ""}
_DROP_CATEGORIES = {"So", "Sk", "Cf", "Co", "Cs"}


class ParseError(ValueError):
    pass


def normalize_name(name: str) -> str:
    """Display form: emoji/symbol decorations removed, whitespace collapsed."""
    text = unicodedata.normalize("NFC", str(name))
    text = "".join(c for c in text if unicodedata.category(c) not in _DROP_CATEGORIES and c not in "︎️")
    return " ".join(text.split())


def player_key(name: str) -> str:
    """Identity form: also ignores case and diacritics ("Jiri" == "Jiří")."""
    decomposed = unicodedata.normalize("NFKD", normalize_name(name))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def player_id(name: str) -> str:
    return hashlib.sha256(player_key(name).encode("utf-8")).hexdigest()[:12]


def _int(value, field: str, line: str) -> int:
    try:
        n = int(str(value).strip() or 0)
    except ValueError:
        raise ParseError(f"{line}: '{field}' must be a whole number, got '{value}'") from None
    if n < 0:
        raise ParseError(f"{line}: '{field}' cannot be negative")
    return n


def _parse_result(result: str, line: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*[-:/]\s*(\d+)(?:\s*[-:/]\s*(\d+))?\s*", result or "")
    if not m:
        raise ParseError(f"{line}: result '{result}' must look like 2-1 or 2-1-0")
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def _raw_match(rnd, p1, p2, w1, w2, d, line) -> dict:
    p1 = normalize_name(p1 or "")
    p2 = normalize_name(p2 or "")
    if not p1:
        raise ParseError(f"{line}: player1 is empty")
    if p2.casefold() in BYE_NAMES:
        p2 = ""
    try:
        rnd = int(rnd)
    except (TypeError, ValueError):
        raise ParseError(f"{line}: round must be a number, got '{rnd}'") from None
    if rnd < 1:
        raise ParseError(f"{line}: round must be >= 1")
    return {"round": rnd, "p1": p1, "p2": p2 or None, "p1Wins": w1, "p2Wins": w2, "draws": d, "line": line}


def parse_csv(content: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(content.strip()))
    if not reader.fieldnames:
        raise ParseError("CSV is empty")
    aliases = {
        "round": "round", "rnd": "round",
        "player1": "player1", "p1": "player1", "player 1": "player1",
        "player2": "player2", "p2": "player2", "player 2": "player2",
        "player1_wins": "player1_wins", "p1_wins": "player1_wins", "player1wins": "player1_wins",
        "player2_wins": "player2_wins", "p2_wins": "player2_wins", "player2wins": "player2_wins",
        "draws": "draws", "result": "result",
    }
    columns = {f: aliases.get(f.strip().casefold()) for f in reader.fieldnames}
    present = set(columns.values())
    missing = {"round", "player1", "player2"} - present
    if missing:
        raise ParseError(f"CSV is missing column(s): {', '.join(sorted(missing))}")
    if "result" not in present and not {"player1_wins", "player2_wins"} <= present:
        raise ParseError("CSV needs either a 'result' column or 'player1_wins' and 'player2_wins' columns")

    matches = []
    for i, row in enumerate(reader, start=2):
        line = f"CSV line {i}"
        r = {columns[k]: (v or "").strip() for k, v in row.items() if k in columns and columns[k]}
        if not any(r.values()):
            continue
        if "result" in r and r["result"]:
            w1, w2, d = _parse_result(r["result"], line)
        else:
            w1 = _int(r.get("player1_wins"), "player1_wins", line)
            w2 = _int(r.get("player2_wins"), "player2_wins", line)
            d = _int(r.get("draws", 0), "draws", line)
        matches.append(_raw_match(r.get("round"), r.get("player1"), r.get("player2"), w1, w2, d, line))
    return matches


def _json_match(m: dict, rnd, line: str) -> dict:
    if not isinstance(m, dict):
        raise ParseError(f"{line}: match must be an object")
    get = lambda *keys, default=None: next((m[k] for k in keys if k in m), default)  # noqa: E731
    p1 = get("player1", "p1")
    p2 = get("player2", "p2")
    if "result" in m:
        w1, w2, d = _parse_result(str(m["result"]), line)
    else:
        w1 = _int(get("player1Wins", "p1Wins", "player1_wins", default=0), "player1Wins", line)
        w2 = _int(get("player2Wins", "p2Wins", "player2_wins", default=0), "player2Wins", line)
        d = _int(get("draws", default=0), "draws", line)
    return _raw_match(get("round", default=rnd), p1, p2, w1, w2, d, line)


def parse_json(content: str) -> tuple[list[dict], dict]:
    """Return (matches, metadata overrides such as name/date/type)."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ParseError(f"Invalid JSON: {e}") from None
    if isinstance(data, list):
        data = {"matches": data}
    if not isinstance(data, dict):
        raise ParseError("JSON must be an object or a list of matches")
    matches = []
    for ri, rnd in enumerate(data.get("rounds") or [], start=1):
        number = rnd.get("round", ri)
        for mi, m in enumerate(rnd.get("matches") or [], start=1):
            matches.append(_json_match(m, number, f"round {number} match {mi}"))
    for mi, m in enumerate(data.get("matches") or [], start=1):
        matches.append(_json_match(m, None, f"match {mi}"))
    meta = {k: data[k] for k in ("name", "date", "type") if data.get(k)}
    return matches, meta


_ROUND_RE = re.compile(r"^\s*(?:round|rd|r)\.?\s*(\d+)\s*:?\s*$", re.IGNORECASE)
_BYE_RE = re.compile(r"^\s*(?P<p1>.+?)\s*(?:[-:]\s*)?\(?\bbye\b\)?\s*$", re.IGNORECASE)
_MATCH_RE = re.compile(
    r"^\s*(?P<p1>.+?)\s+(?:vs\.?|v\.?|-)\s+(?P<p2>.+?)\s*[:,]?\s+"
    r"(?P<result>\d+\s*[-:/]\s*\d+(?:\s*[-:/]\s*\d+)?)\s*$",
    re.IGNORECASE,
)


def parse_text(content: str) -> list[dict]:
    matches = []
    current_round = None
    inferred_round = 1
    seen_in_round: set[str] = set()
    has_headers = any(_ROUND_RE.match(l) for l in content.splitlines())
    for i, raw in enumerate(content.splitlines(), start=1):
        line_text = raw.strip()
        if not line_text or line_text.startswith("#"):
            continue
        line = f"line {i}"
        if m := _ROUND_RE.match(line_text):
            current_round = int(m.group(1))
            continue
        if m := _MATCH_RE.match(line_text):
            p1, p2 = m.group("p1"), m.group("p2")
            w1, w2, d = _parse_result(m.group("result"), line)
        elif m := _BYE_RE.match(line_text):
            p1, p2, w1, w2, d = m.group("p1"), None, 2, 0, 0
        else:
            raise ParseError(f"{line}: cannot understand '{line_text}'. Expected 'Alice vs Bob 2-1-0' or 'Alice - BYE'")
        if has_headers:
            if current_round is None:
                raise ParseError(f"{line}: match appears before the first 'Round N' header")
            rnd = current_round
        else:
            names = {player_key(p1), player_key(p2 or "")} - {""}
            if names & seen_in_round:
                inferred_round += 1
                seen_in_round = set()
            seen_in_round |= names
            rnd = inferred_round
        matches.append(_raw_match(rnd, p1, p2, w1, w2, d, line))
    return matches


_RECORD_RE = re.compile(r"^(\d+)\s*[–—-]\s*(\d+)\s*[–—-]\s*(\d+)$")
_SCORE_RE = re.compile(r"^\d{2,3}$")


def parse_eventlink(content: str) -> list[dict]:
    """Pairings copied from Eventlink/Companion, one value per line:

        1               table number (sometimes missing, e.g. playoffs)
        Alice           player A
        1–0–0           A's record after this match (W–L–D)
        21              game score: A won 2, B won 1 (optional 3rd digit = drawn games)
        Bob             player B, or "Bye"
        0–1–0           B's record (absent for a bye)

    Rounds are listed in order, so a new round starts when a player appears again.
    Neither table numbers (playoff brackets reuse them) nor records (unreported results
    leave them unchanged) are reliable for this. A file with a second event pasted after
    the first (table 1 with both records back at one match) is rejected.
    """
    lines = [(i, l.strip()) for i, l in enumerate(content.splitlines(), start=1) if l.strip()]
    kind = lambda t: "R" if _RECORD_RE.match(t) else "N" if t.isdigit() else "S"  # noqa: E731
    rounds_of = lambda rec: sum(int(x) for x in _RECORD_RE.match(rec).groups())  # noqa: E731

    matches: list[dict] = []
    pos, rnd = 0, 0
    seen: set[str] = set()
    while pos < len(lines):
        start_line = lines[pos][0]
        table = None
        if kind(lines[pos][1]) == "N":
            table = int(lines[pos][1])
            pos += 1
            if pos == len(lines):
                break  # dangling table number at the very end
        window = [t for _, t in lines[pos:pos + 5]]
        shape = "".join(kind(t) for t in window)
        line = f"line {start_line}"
        if shape.startswith("SRN") and len(window) >= 4 and window[3].casefold() == "bye":
            p1, recs, p2 = window[0], [window[1]], None
            w1, w2, d = 2, 0, 0
            pos += 4
        elif shape == "SRNSR":
            p1, rec1, score, p2, rec2 = window
            if not _SCORE_RE.match(score):
                raise ParseError(f"line {lines[pos + 2][0]}: game score '{score}' should look like 21 or 02")
            w1, w2, d = int(score[0]), int(score[1]), int(score[2]) if len(score) == 3 else 0
            recs = [rec1, rec2]
            pos += 5
        else:
            raise ParseError(
                f"{line}: expected a match block (table, player, W–L–D record, score like 21, player, record), "
                f"got: {' | '.join(window)}"
            )
        if rnd >= 2 and table == 1 and all(rounds_of(r) == 1 for r in recs):
            raise SecondEventError(start_line)
        keys = {player_key(p1)} | ({player_key(p2)} if p2 else set())
        if rnd == 0 or keys & seen:
            rnd, seen = rnd + 1, set()
        seen |= keys
        matches.append(_raw_match(rnd, p1, p2, w1, w2, d, line))
    return matches


class SecondEventError(ParseError):
    def __init__(self, line_no: int):
        super().__init__(f"line {line_no}: a second event seems to start here (records restart at 1 match). "
                         "Upload each event separately.")
        self.line_no = line_no


def split_eventlink(content: str) -> list[str]:
    """Split an Eventlink paste that contains several events into one text per event."""
    parts: list[str] = []
    lines = content.splitlines()
    while True:
        try:
            parse_eventlink("\n".join(lines))
        except SecondEventError as err:
            parts.append("\n".join(lines[:err.line_no - 1]))
            lines = lines[err.line_no - 1:]
            continue
        parts.append("\n".join(lines))
        return parts


def _looks_like_eventlink(content: str) -> bool:
    lines = [l.strip() for l in content.splitlines() if l.strip()][:30]
    return sum(bool(_RECORD_RE.match(l)) for l in lines) >= max(2, len(lines) // 4)


def detect_format(content: str) -> str:
    stripped = content.lstrip()
    if stripped.startswith(("{", "[")):
        return "json"
    if _looks_like_eventlink(stripped):
        return "eventlink"
    first = stripped.splitlines()[0].casefold() if stripped else ""
    if "," in first and "player1" in first.replace(" ", "") or first.startswith("round,"):
        return "csv"
    return "text"


def _validate(matches: list[dict]) -> None:
    if not matches:
        raise ParseError("No matches found")
    seen: dict[tuple[int, str], str] = {}
    for m in matches:
        if m["p2"] and player_key(m["p1"]) == player_key(m["p2"]):
            raise ParseError(f"{m['line']}: a player cannot play against themselves ({m['p1']})")
        for p in filter(None, (m["p1"], m["p2"])):
            key = (m["round"], player_key(p))
            if key in seen:
                raise ParseError(f"{m['line']}: {p} already plays in round {m['round']} ({seen[key]})")
            seen[key] = m["line"]


def compute_standings(players: dict[str, str], matches: list[dict]) -> list[dict]:
    """Standings by match points, then opponents' match-win % (floor 0.33), then game-win %."""
    rec = {pid: {"playerId": pid, "points": 0, "wins": 0, "losses": 0, "draws": 0, "byes": 0,
                 "gamesWon": 0, "gamesPlayed": 0, "rounds": 0, "opponents": []} for pid in players}
    for m in matches:
        a, b = m["p1"], m["p2"]
        if b is None:
            r = rec[a]
            r["byes"] += 1
            r["points"] += 3
            r["rounds"] += 1
            r["gamesWon"] += 2
            r["gamesPlayed"] += 2
            continue
        games = m["p1Wins"] + m["p2Wins"] + m["draws"]
        for me, opp, won, lost in ((a, b, m["p1Wins"], m["p2Wins"]), (b, a, m["p2Wins"], m["p1Wins"])):
            r = rec[me]
            r["rounds"] += 1
            r["opponents"].append(opp)
            r["gamesWon"] += won
            r["gamesPlayed"] += games
            if won > lost:
                r["wins"] += 1
                r["points"] += 3
            elif won < lost:
                r["losses"] += 1
            else:
                r["draws"] += 1
                r["points"] += 1

    def mwp(pid):
        r = rec[pid]
        return max(0.33, r["points"] / (3 * r["rounds"])) if r["rounds"] else 0.33

    rows = []
    for pid, r in rec.items():
        omw = sum(mwp(o) for o in r["opponents"]) / len(r["opponents"]) if r["opponents"] else 0.0
        gw = r["gamesWon"] / r["gamesPlayed"] if r["gamesPlayed"] else 0.0
        rows.append({k: r[k] for k in ("playerId", "points", "wins", "losses", "draws", "byes")}
                    | {"omw": round(omw, 4), "gw": round(gw, 4)})
    rows.sort(key=lambda r: (-r["points"], -r["omw"], -r["gw"], players[r["playerId"]].casefold()))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", player_key(text)).strip("-")[:40] or "tournament"


def build_tournament(
    *, content: str, name: str = "", date: str = "", type: str = "", fmt: str = "auto",
    uploaded_by: str = "", uploaded_at: str = "",
) -> dict:
    """Parse an upload and return the normalized tournament (see ARCHITECTURE.md §3.1)."""
    content = (content or "").lstrip("﻿")
    if not content.strip():
        raise ParseError("Upload is empty")
    fmt = (fmt or "auto").lower()
    if fmt == "auto":
        fmt = detect_format(content)
    meta = {}
    if fmt == "csv":
        raw = parse_csv(content)
    elif fmt == "json":
        raw, meta = parse_json(content)
    elif fmt == "text":
        raw = parse_text(content)
    elif fmt == "eventlink":
        raw = parse_eventlink(content)
    else:
        raise ParseError(f"Unknown format '{fmt}'. Use one of: auto, {', '.join(FORMATS)}")

    name = normalize_name(name or meta.get("name", ""))
    date = (date or meta.get("date", "")).strip()
    type = (type or meta.get("type", "")).strip().lower()
    if not name:
        raise ParseError("Tournament name is required")
    try:
        date_cls.fromisoformat(date)
    except ValueError:
        raise ParseError(f"Date must be YYYY-MM-DD, got '{date}'") from None
    if type not in TOURNAMENT_TYPES:
        raise ParseError(f"Type must be one of {', '.join(TOURNAMENT_TYPES)}, got '{type}'")

    _validate(raw)
    players: dict[str, str] = {}
    matches = []
    for m in sorted(raw, key=lambda m: m["round"]):
        ids = []
        for p in (m["p1"], m["p2"]):
            if p is None:
                ids.append(None)
                continue
            pid = player_id(p)
            players.setdefault(pid, p)
            ids.append(pid)
        matches.append({"round": m["round"], "p1": ids[0], "p2": ids[1],
                        "p1Wins": m["p1Wins"], "p2Wins": m["p2Wins"], "draws": m["draws"]})

    fingerprint = hashlib.sha256(
        json.dumps([name.casefold(), date, matches], sort_keys=True).encode()
    ).hexdigest()[:6]
    return {
        "id": f"{date}-{_slug(name)}-{fingerprint}",
        "name": name,
        "date": date,
        "type": type,
        "sourceFormat": fmt,
        "uploadedAt": uploaded_at,
        "uploadedBy": uploaded_by,
        "players": players,
        "matches": matches,
        "standings": compute_standings(players, matches),
    }
