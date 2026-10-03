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


def dist_to_score(km: float) -> int:
    return int(5000 * math.exp(-0.000673 * km) + 0.5)


def score_label(score: int) -> str:
    if score <= 2500:
        return translate("Terrible")
    if score <= 3750:
        return translate("Decent")
    if score <= 4375:
        return translate("High")
    if score <= 4713:
        return translate("Exceptional")
    if score <= 4857:
        return translate("Elite")
    return translate("Inhuman")


def score_tiers() -> list[dict]:
    """The tier ladder (same thresholds as score_label), lowest first. `max` is the upper bound."""
    return [
        {"label": translate("Terrible"), "max": 2500},
        {"label": translate("Decent"), "max": 3750},
        {"label": translate("High"), "max": 4375},
        {"label": translate("Exceptional"), "max": 4713},
        {"label": translate("Elite"), "max": 4857},
        {"label": translate("Inhuman"), "max": 5000},
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
