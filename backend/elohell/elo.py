"""Elo rating model.

Bump MODEL_VERSION whenever a constant or rule changes, then trigger a full
recalculation (POST /api/admin/recalculate).
"""
from dataclasses import asdict, dataclass

MODEL_VERSION = "1.0"


@dataclass(frozen=True)
class EloConfig:
    initial_rating: float = 1500.0
    k_factor: float = 32.0
    provisional_k_factor: float = 48.0
    provisional_matches: int = 10
    scale: float = 400.0
    casual_weight: float = 1.0

    def to_dict(self) -> dict:
        return {"modelVersion": MODEL_VERSION, **asdict(self)}


DEFAULT_CONFIG = EloConfig()

# Ladder name -> tournament types included in it.
LADDERS: dict[str, frozenset[str]] = {
    "rel": frozenset({"rel"}),
    "all": frozenset({"rel", "casual"}),
}

TOURNAMENT_TYPES = ("rel", "casual")


def expected_score(rating: float, opponent_rating: float, scale: float = 400.0) -> float:
    return 1.0 / (1.0 + 10 ** ((opponent_rating - rating) / scale))


def k_factor(cfg: EloConfig, matches_played: int) -> float:
    if matches_played < cfg.provisional_matches:
        return cfg.provisional_k_factor
    return cfg.k_factor


def rate_match(
    cfg: EloConfig,
    rating_a: float,
    rating_b: float,
    score_a: float,
    played_a: int,
    played_b: int,
    weight: float = 1.0,
) -> tuple[float, float]:
    """Return new (rating_a, rating_b). score_a is 1 (A won), 0.5 (draw) or 0."""
    exp_a = expected_score(rating_a, rating_b, cfg.scale)
    new_a = rating_a + k_factor(cfg, played_a) * weight * (score_a - exp_a)
    new_b = rating_b + k_factor(cfg, played_b) * weight * ((1 - score_a) - (1 - exp_a))
    return new_a, new_b
