"""Membership perks (docs/ARCHITECTURE.md §7): validation and derived data, no storage.

Everyone with a claimed profile: flair. Diamond: private match notes, matchup stats by
archetype, CSV export, custom profile URL, posting upcoming tournaments.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date as date_cls, timedelta

from .engine import winrate
from .schedule import RECURRENCES

LEVELS = {"": 0, "supporter": 1, "diamond": 2}
FLAIR_COLORS = ("blue", "green", "purple", "orange", "red", "gray")
SLUG_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])")
UNKNOWN_DECK = "Unknown deck"
MAX_UPCOMING_PER_MEMBER = 10


class Invalid(ValueError):
    pass


def at_least(level: str, required: str) -> bool:
    return LEVELS.get(level or "", 0) >= LEVELS[required]


def best_level(*levels: str) -> str:
    return max((l or "" for l in levels), key=lambda l: LEVELS.get(l, 0), default="")


def text(value, field: str, max_len: int, required: bool = False) -> str:
    out = " ".join(str(value or "").split())
    if required and not out:
        raise Invalid(f"{field} is required")
    if len(out) > max_len or any(not c.isprintable() for c in out):
        raise Invalid(f"{field} must be at most {max_len} printable characters")
    return out


def icon(value) -> str:
    """A short Unicode icon such as an emoji or a flag (🇨🇿 is two code points, family emoji up
    to ~11): no spaces or letters/digits, at most 16 code points. Empty: no icon."""
    out = str(value or "").strip()
    if len(out) > 16 or any(c.isspace() or c.isalnum() or not c.isprintable() and c not in "\u200d\ufe0f" for c in out):
        raise Invalid("Icon must be an emoji or symbol (for example 🇨🇿 or 🏝)")
    return out


def flair_color(value) -> str:
    value = str(value or "")
    if value and value not in FLAIR_COLORS:
        raise Invalid(f"Flair colour must be one of {', '.join(FLAIR_COLORS)}")
    return value


def slug(value) -> str:
    out = str(value or "").strip().lower()
    if out and not SLUG_RE.fullmatch(out):
        raise Invalid("Profile URL name: 3-30 lowercase letters, digits or dashes, not starting or ending with a dash")
    return out


def upcoming_event(body: dict, today: date_cls, parse_game_format, parse_link) -> dict:
    """Validated fields of an upcoming tournament (dates from today up to a year ahead).
    recurrence: "weekly" / "monthly" repeats from the first date (see delo/schedule.py)."""
    try:
        day = date_cls.fromisoformat(str(body.get("date", "")))
    except ValueError:
        raise Invalid("Date must be YYYY-MM-DD") from None
    if not today <= day <= today + timedelta(days=366):
        raise Invalid("Date must be between today and one year from now")
    kind = str(body.get("type") or "").lower()
    if kind not in ("", "rel", "casual"):
        raise Invalid("Type must be rel or casual")
    recurrence = str(body.get("recurrence") or "").lower()
    if recurrence not in ("", *RECURRENCES):
        raise Invalid(f"Repeats must be one of: {', '.join(RECURRENCES)}")
    return {
        "name": text(body.get("name"), "Name", 100, required=True),
        "date": day.isoformat(),
        "time": text(body.get("time"), "Time", 20),
        "location": text(body.get("location"), "Location", 100),
        "format": parse_game_format(body.get("format")),
        "type": kind or None,
        "link": parse_link(body.get("link")),
        "recurrence": recurrence or None,
    }


def archetype_stats(tournaments: list[dict], decks: dict[str, dict[str, str]]) -> dict:
    """Match record by opponent's deck and by own deck.

    tournaments: the player's ladder-entry tournament rows (with matches);
    decks: {tournamentId: {playerId: deck}} for the tournaments, including the player's own.
    """
    by_opponent: dict[str, list[int]] = {}
    by_own: dict[str, list[int]] = {}
    for t in tournaments:
        tdecks = decks.get(t["tournamentId"], {})
        own = t.get("deck") or UNKNOWN_DECK
        for m in t["matches"]:
            if m["result"] not in ("W", "L", "D"):
                continue  # byes
            i = "WLD".index(m["result"])
            for table, key in ((by_opponent, tdecks.get(m["opponentId"]) or UNKNOWN_DECK), (by_own, own)):
                table.setdefault(key, [0, 0, 0])[i] += 1

    def rows(table):
        out = [{"deck": k, "wins": w, "losses": l, "draws": d, "matches": w + l + d,
                "winrate": winrate(w, l)} for k, (w, l, d) in table.items()]
        return sorted(out, key=lambda r: (r["deck"] == UNKNOWN_DECK, -r["matches"], r["deck"].casefold()))

    return {"byOpponentDeck": rows(by_opponent), "byOwnDeck": rows(by_own)}


EXPORT_COLUMNS = ("date", "tournament", "format", "type", "round", "opponent", "result", "score",
                  "my_deck", "opponent_deck", "elo_before", "elo_after", "elo_change", "note")


def history_csv(tournaments: list[dict], history: list[dict], names: dict[str, str],
                decks: dict[str, dict[str, str]], notes: dict[str, dict[str, str]], pid: str) -> str:
    """CSV of every match (oldest first), with decks, ratings and the player's private notes."""
    ratings = {(h["tournamentId"], h["round"]): h for h in history}
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(EXPORT_COLUMNS)
    for t in sorted(tournaments, key=lambda t: t["date"]):
        tdecks = decks.get(t["tournamentId"], {})
        for m in sorted(t["matches"], key=lambda m: m["round"]):
            h = ratings.get((t["tournamentId"], m["round"]), {})
            writer.writerow([
                t["date"], t["name"], t.get("format") or "", t.get("type") or "", m["round"],
                names.get(m["opponentId"], "") if m["opponentId"] else "BYE", m["result"], m["score"],
                tdecks.get(pid, ""), tdecks.get(m["opponentId"], "") if m["opponentId"] else "",
                h.get("ratingBefore", ""), h.get("ratingAfter", ""), h.get("delta", ""),
                notes.get(t["tournamentId"], {}).get(str(m["round"]), ""),
            ])
    return out.getvalue()
