"""Elo rating model.

Bump MODEL_VERSION whenever a constant or rule changes, then trigger a full
recalculation (POST /api/admin/recalculate). Uploads rate incrementally only while the
stored ratings were computed with the current MODEL_VERSION.
"""
from dataclasses import asdict, dataclass

MODEL_VERSION = "2.1"  # 2.0: scale 1135.77 (200 points = 60%), was 400; 2.1: draws are not rated


@dataclass(frozen=True)
class EloConfig:
    initial_rating: float = 1500.0
    k_factor: float = 32.0
    provisional_k_factor: float = 48.0
    provisional_matches: int = 10
    # A 200-point rating difference = 60% win expectancy (chess uses 400: 10-to-1 odds per
    # 400 points). Same normalisation as the MTG Elo Project / Quick_MTGStat's PT Elo table.
    scale: float = 1135.77
    casual_weight: float = 1.0
    # Rated matches needed for a rank and a shown Elo; until then a player is provisional.
    # Display only: ratings count from the first match, so this needs no MODEL_VERSION bump.
    min_matches: int = 10

    def to_dict(self) -> dict:
        return {"modelVersion": MODEL_VERSION, **asdict(self)}


DEFAULT_CONFIG = EloConfig()

# Ladder name -> tournament types included in it.
LADDERS: dict[str, frozenset[str]] = {
    "rel": frozenset({"rel"}),
    "all": frozenset({"rel", "casual"}),
}

TOURNAMENT_TYPES = ("rel", "casual")

# Game format id -> display name. Each ladder above also exists per format as "<ladder>:<format>",
# rating only that format's tournaments; the plain ladder is the global one (every format).
GAME_FORMATS: dict[str, str] = {
    "modern": "Modern",
    "limited": "Limited",
    "duel-commander": "Duel Commander",
    "edh": "EDH",
    "legacy": "Legacy",
    "vintage": "Vintage",
    "premodern": "Premodern",
}

# Islands: groups of players who play each other but not other groups (usually a country).
# Every ladder exists per island, "<island>/<ladder>[:<format>]", and rates only that island's
# tournaments, so Elo numbers are never compared across islands. Tournaments without an island
# (before any island was created) use the plain ids below.
LADDER_IDS: tuple[str, ...] = tuple(LADDERS) + tuple(f"{l}:{f}" for l in LADDERS for f in GAME_FORMATS)


def ladder_ids(island: str = "") -> tuple[str, ...]:
    return tuple(f"{island}/{lid}" for lid in LADDER_IDS) if island else LADDER_IDS


def ladder_id(ladder: str, game_format: str | None = None, island: str = "") -> str:
    lid = f"{ladder}:{game_format}" if game_format else ladder
    return f"{island}/{lid}" if island else lid


def ladder_includes(lid: str, tournament: dict) -> bool:
    island, _, rest = lid.rpartition("/")
    ladder, _, game_format = rest.partition(":")
    return ((tournament.get("island") or "") == island and tournament["type"] in LADDERS[ladder]
            and game_format in ("", tournament.get("format")))


def ladders_for(tournament: dict) -> list[str]:
    """Ladder ids whose ratings change when this tournament is rated."""
    return [lid for lid in ladder_ids(tournament.get("island") or "") if ladder_includes(lid, tournament)]


def expected_score(rating: float, opponent_rating: float, scale: float = DEFAULT_CONFIG.scale) -> float:
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
