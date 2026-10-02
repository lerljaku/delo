"""Replay tournaments through the Elo model and derive per-player stats.

compute_all replays everything from scratch. apply_tournament continues from stored ladder
entries (their "state" holds the unrounded engine state) and gives the same result as a full
replay, provided the new tournament sorts after every tournament already rated.

Pure functions only: storage is handled by the caller.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .elo import DEFAULT_CONFIG, LADDER_IDS, MODEL_VERSION, EloConfig, ladder_includes, ladders_for, rate_match


def _r(x: float) -> float:
    return round(x, 1)


@dataclass
class _PlayerState:
    rating: float
    peak: float
    peak_date: str = ""
    matches: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    game_wins: int = 0
    game_losses: int = 0
    game_draws: int = 0
    cur_win: int = 0
    cur_loss: int = 0
    best_win: int = 0
    best_loss: int = 0
    opponents: dict = field(default_factory=dict)   # opponentId -> [w, l, d]
    history: list = field(default_factory=list)     # rows added by this replay only
    tournaments: list = field(default_factory=list)

    @classmethod
    def restore(cls, entry: dict) -> "_PlayerState":
        s, st = entry["stats"], entry["state"]
        return cls(
            rating=st["rating"], peak=st["peak"], peak_date=s["peakDate"],
            matches=s["matches"], wins=s["wins"], losses=s["losses"], draws=s["draws"],
            game_wins=s["gameWins"], game_losses=s["gameLosses"], game_draws=s["gameDraws"],
            cur_win=st["curWin"], cur_loss=st["curLoss"],
            best_win=s["longestWinStreak"], best_loss=s["longestLossStreak"],
            opponents={opp: list(wld) for opp, wld in st["opponents"].items()},
            tournaments=list(reversed(entry["tournaments"])),
        )

    def record(self, result: str, won: int, lost: int, drawn: int, opponent: str) -> None:
        self.matches += 1
        self.game_wins += won
        self.game_losses += lost
        self.game_draws += drawn
        o = self.opponents.setdefault(opponent, [0, 0, 0])
        if result == "W":
            self.wins += 1
            o[0] += 1
            self.cur_win += 1
            self.cur_loss = 0
        elif result == "L":
            self.losses += 1
            o[1] += 1
            self.cur_loss += 1
            self.cur_win = 0
        else:
            self.draws += 1
            o[2] += 1
            self.cur_win = self.cur_loss = 0
        self.best_win = max(self.best_win, self.cur_win)
        self.best_loss = max(self.best_loss, self.cur_loss)


def _matchups(opponents: dict) -> tuple[dict | None, dict | None]:
    rows = []
    for opp, (w, l, d) in opponents.items():
        n = w + l + d
        rows.append({"playerId": opp, "wins": w, "losses": l, "draws": d, "matches": n,
                     "score": round((w + 0.5 * d) / n, 4)})
    min_n = 2 if any(r["matches"] >= 2 for r in rows) else 1
    rows = [r for r in rows if r["matches"] >= min_n]
    good = [r for r in rows if r["score"] >= 0.5]
    bad = [r for r in rows if r["score"] < 0.5]
    best = max(good, key=lambda r: (r["score"], r["matches"])) if good else None
    worst = min(bad, key=lambda r: (r["score"], -r["matches"])) if bad else None
    return best, worst


def _result(won: int, lost: int) -> str:
    return "W" if won > lost else "L" if won < lost else "D"


def sort_key(t: dict) -> tuple[str, str, str]:
    """Rating order. Works for full tournaments and for stored tournament metadata."""
    return t["date"], t.get("uploadedAt", ""), t.get("id") or t["tournamentId"]


def sort_tournaments(tournaments: list[dict]) -> list[dict]:
    return sorted(tournaments, key=sort_key)


def can_resume(entry: dict) -> bool:
    return entry.get("state", {}).get("modelVersion") == MODEL_VERSION


def compute_ladder(tournaments: list[dict], lid: str, cfg: EloConfig,
                   prior: dict[str, dict] | None = None) -> dict[str, dict]:
    """Return {playerId: ladder entry} for one ladder.

    `prior` ({playerId: stored entry}) resumes from stored ratings; each returned entry's
    history then holds only the rows added by these tournaments.
    """
    states = {pid: _PlayerState.restore(e) for pid, e in (prior or {}).items()}

    def state(pid: str) -> _PlayerState:
        if pid not in states:
            states[pid] = _PlayerState(rating=cfg.initial_rating, peak=cfg.initial_rating)
        return states[pid]

    for t in sort_tournaments(tournaments):
        if not ladder_includes(lid, t):
            continue
        weight = cfg.casual_weight if t["type"] == "casual" else 1.0
        standings = {s["playerId"]: s for s in t.get("standings", [])}
        start = {pid: state(pid).rating for pid in t["players"]}
        per_tournament: dict[str, list] = {pid: [] for pid in t["players"]}

        for m in sorted(t["matches"], key=lambda m: m["round"]):
            a, b = m["p1"], m["p2"]
            if b is None:
                per_tournament[a].append({"round": m["round"], "opponentId": None, "result": "BYE",
                                          "score": "", "delta": 0.0})
                continue
            sa, sb = state(a), state(b)
            res_a = _result(m["p1Wins"], m["p2Wins"])
            score_a = {"W": 1.0, "L": 0.0, "D": 0.5}[res_a]
            before_a, before_b = sa.rating, sb.rating
            sa.rating, sb.rating = rate_match(cfg, before_a, before_b, score_a, sa.matches, sb.matches, weight)
            for me, opp, st, before, won, lost, res in (
                (a, b, sa, before_a, m["p1Wins"], m["p2Wins"], res_a),
                (b, a, sb, before_b, m["p2Wins"], m["p1Wins"], _result(m["p2Wins"], m["p1Wins"])),
            ):
                st.record(res, won, lost, m["draws"], opp)
                if st.rating > st.peak:
                    st.peak, st.peak_date = st.rating, t["date"]
                score = f"{won}-{lost}-{m['draws']}"
                delta = _r(st.rating - before)
                st.history.append({
                    "seq": st.matches, "date": t["date"], "tournamentId": t["id"],
                    "round": m["round"], "opponentId": opp, "result": res, "score": score,
                    "ratingBefore": _r(before), "ratingAfter": _r(st.rating), "delta": delta,
                })
                per_tournament[me].append({"round": m["round"], "opponentId": opp, "result": res,
                                           "score": score, "delta": delta})

        for pid, matches in per_tournament.items():
            st = state(pid)
            s = standings.get(pid, {})
            st.tournaments.append({
                "tournamentId": t["id"], "name": t["name"], "date": t["date"], "type": t["type"],
                "format": t.get("format"),
                "wins": sum(x["result"] == "W" for x in matches),
                "losses": sum(x["result"] == "L" for x in matches),
                "draws": sum(x["result"] == "D" for x in matches),
                "byes": sum(x["result"] == "BYE" for x in matches),
                "rank": s.get("rank"), "playerCount": len(t["players"]),
                "ratingBefore": _r(start[pid]), "ratingAfter": _r(st.rating),
                "delta": _r(st.rating - start[pid]), "matches": matches,
            })

    out = {}
    for pid, st in states.items():
        best, worst = _matchups(st.opponents)
        games = st.game_wins + st.game_losses + st.game_draws
        out[pid] = {
            "rating": _r(st.rating),
            "stats": {
                "matches": st.matches, "wins": st.wins, "losses": st.losses, "draws": st.draws,
                "winrate": round(st.wins / st.matches, 4) if st.matches else 0.0,
                "games": games, "gameWins": st.game_wins, "gameLosses": st.game_losses,
                "gameDraws": st.game_draws,
                "peakRating": _r(st.peak), "peakDate": st.peak_date,
                "longestWinStreak": st.best_win, "longestLossStreak": st.best_loss,
                "bestMatchup": best, "worstMatchup": worst,
                "tournamentCount": len(st.tournaments),
            },
            "tournaments": list(reversed(st.tournaments)),
            "history": st.history,
            "state": {"modelVersion": MODEL_VERSION, "rating": st.rating, "peak": st.peak,
                      "curWin": st.cur_win, "curLoss": st.cur_loss, "opponents": st.opponents},
        }
    return out


def _names(tournaments: list[dict], spellings: dict[str, dict]) -> dict:
    """Count each player's spellings ({name: [count, *sort_key of last use]}) and pick the
    display name: the most frequently used spelling, ties going to the most recently used.
    """
    touched = set()
    for t in sort_tournaments(tournaments):
        for pid, name in t["players"].items():
            counts = spellings.setdefault(pid, {})
            counts[name] = [counts.get(name, [0])[0] + 1, *sort_key(t)]
            touched.add(pid)
    return {
        "names": {pid: max(spellings[pid].items(), key=lambda kv: kv[1])[0] for pid in touched},
        "spellings": {pid: spellings[pid] for pid in touched},
    }


def compute_all(tournaments: list[dict], cfg: EloConfig = DEFAULT_CONFIG) -> dict:
    """Return {"names": {playerId: name}, "spellings": {playerId: {...}},
    "ladders": {ladderId: {playerId: entry}}} with every ladder, including empty ones.
    """
    return {
        **_names(tournaments, {}),
        "ladders": {lid: compute_ladder(tournaments, lid, cfg) for lid in LADDER_IDS},
    }


def apply_tournament(t: dict, prior: dict[str, dict[str, dict]], spellings: dict[str, dict],
                     cfg: EloConfig = DEFAULT_CONFIG) -> dict:
    """Rate one more tournament on top of stored results; same shape as compute_all, but only
    with the ladders and players it touches.

    prior: {ladderId: {playerId: stored entry}} for the players of `t` in ladders_for(t).
    spellings: {playerId: stored spellings} for the players of `t`.
    """
    return {
        **_names([t], {pid: dict(s) for pid, s in spellings.items()}),
        "ladders": {lid: compute_ladder([t], lid, cfg, prior.get(lid)) for lid in ladders_for(t)},
    }
