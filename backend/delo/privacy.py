"""GDPR erasure: replace a player in stored tournaments with an anonymous "Deleted player".

The player's matches stay (with a new random id, not derived from the name), so every other
player's rating stays exactly the same. See frontend/privacy.html for the user-facing policy.
"""
from __future__ import annotations

import re
import secrets

from . import parsers

ERASED_NAME = "Deleted player"


def new_anonymous_id() -> str:
    """Random, so it cannot be traced back to the name (normal ids are a hash of the name)."""
    return secrets.token_hex(6)


def anonymize(t: dict, pid: str, new_id: str) -> dict:
    """Copy of tournament t with player pid replaced by new_id / ERASED_NAME."""
    swap = lambda x: new_id if x == pid else x  # noqa: E731
    out = dict(t)
    out["players"] = {swap(p): (ERASED_NAME if p == pid else name) for p, name in t["players"].items()}
    out["matches"] = [{**m, "p1": swap(m["p1"]), "p2": swap(m["p2"])} for m in t["matches"]]
    out["standings"] = [{**s, "playerId": swap(s["playerId"])} for s in t.get("standings", [])]
    return out


def _player_ids(raw: str, source_format: str) -> set[str]:
    if source_format == "json":
        matches, _ = parsers.parse_json(raw)
    else:
        matches = {"csv": parsers.parse_csv, "text": parsers.parse_text,
                   "eventlink": parsers.parse_eventlink}[source_format](raw)
    return {parsers.player_id(p) for m in matches for p in (m["p1"], m["p2"]) if p}


def scrub_raw(raw: str, pid: str, spellings, source_format: str) -> str | None:
    """The raw upload with the player's name replaced, or None if the name could not be removed
    reliably (the caller then stores the anonymized normalized tournament instead)."""
    try:
        expected = _player_ids(raw, source_format) - {pid} | {parsers.player_id(ERASED_NAME)}
        for name in sorted(set(spellings), key=len, reverse=True):
            raw = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", ERASED_NAME, raw, flags=re.IGNORECASE)
        # names spelled with other accents or decorations: whole lines that are the player
        raw = "\n".join(ERASED_NAME if l.strip() and parsers.player_id(l) == pid else l for l in raw.split("\n"))
        # nothing else may change, e.g. "Bob" must not have eaten part of "Bob Horvath"
        return raw if _player_ids(raw, source_format) == expected else None
    except (parsers.ParseError, KeyError):
        return None
