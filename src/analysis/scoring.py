"""scoring.py — Distance→score math, rank labels, trend arrows and rolling-window statistics."""

import math
import random
import statistics

from src.i18n.lang import translate

MEDIUM_CONFIDENCE_WINDOW = 30
LOW_CONFIDENCE_WINDOW = 10
HIGH_CONFIDENCE_WINDOW = 100
MIN_ZONE_ROUNDS = 10
BOOTSTRAP_SAMPLES = 1000

# Score cut-offs shared by the tier ladder and the replay event grades (replay_grading.py).
SCORE_CUTOFFS = (3000, 4000, 4500, 4850)

# Distance used for a round with no guess: about half the Earth's circumference (the farthest
# two points can be), so dist_to_score() gives 0 and the round counts as a 0-point round.
NO_GUESS_KM = 20_000.0


def dist_to_score(km: float) -> int:
    return int(5000 * math.exp(-0.000673 * km) + 0.5)


RANK_CUTOFFS = (
    ("Clueless", 1000),
    ("Gullible", 3000),
    ("Sloppy", 4000),
    ("Decent", 4500),
    ("Savvy", 4900),
)
# Above the last cutoff the rank depends on the share of 5K rounds: (label, minimum share), best first.
TOP_RANKS = (("Legendary", 0.5), ("Insane", 0.2), ("Flawless", 0.05))


def score_label(score: int, five_k_share: float = 0.0) -> str:
    for label, cap in RANK_CUTOFFS:
        if score <= cap:
            return translate(label)
    for label, share in TOP_RANKS:
        if five_k_share >= share:
            return translate(label)
    return translate(RANK_CUTOFFS[-1][0])


def score_tiers() -> list[dict]:
    """The tier ladder, lowest first. `max` is the upper bound; the top ranks share 5000."""
    return [
        *({"label": translate(label), "max": cap} for label, cap in RANK_CUTOFFS),
        *({"label": translate(label), "max": 5000} for label, _ in reversed(TOP_RANKS)),
    ]


def arrow(now_val, prev_val, lower_is_better=True) -> str:
    if now_val is None or prev_val is None:
        return "→"
    better = now_val < prev_val if lower_is_better else now_val > prev_val
    worse = now_val > prev_val if lower_is_better else now_val < prev_val
    delta = abs(now_val - prev_val) / (prev_val or 1)
    if delta < 0.05:
        return "→"
    if better:
        return "↑"
    if worse:
        return "↓"
    return "→"


def median(values):
    return round(statistics.median(values), 1) if values else None


def p90(values):
    if not values:
        return None
    s = sorted(values)
    return round(s[min(int(len(s) * 0.9), len(s) - 1)], 1)


def stddev(values):
    return round(statistics.pstdev(values), 1) if len(values) >= 2 else None


def pct(a, b) -> str:
    return f"{round(a / b * 100)}%" if b else "—"


def fmt(val) -> str:
    return f"{val:,}" if val is not None else "—"


def bootstrap_ci(values):
    if len(values) < 20:
        return None
    alpha = 0.025
    medians = sorted(
        statistics.median(random.choices(values, k=len(values))) for _ in range(BOOTSTRAP_SAMPLES)
    )
    return (
        round(medians[int(alpha * BOOTSTRAP_SAMPLES)], 1),
        round(medians[int((1 - alpha) * BOOTSTRAP_SAMPLES)], 1),
    )
