"""
calculator.py — Pure computation functions for GeoGuessr performance analysis.
"""

import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass


def _dist_to_score(km: float) -> int:
    return int(5000 * math.exp(-0.000673 * km) + 0.5)


def _score_label(score: int) -> str:
    if score <= 2500:
        return "Pésimo"
    if score <= 3750:
        return "Decente"
    if score <= 4375:
        return "Alto"
    if score <= 4713:
        return "Excepcional"
    if score <= 4857:
        return "Elite"
    return "Inhumano"


def _arrow(now_val, prev_val, lower_is_better=True) -> str:
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


def _median(values):
    return round(statistics.median(values), 1) if values else None


def _p90(values):
    if not values:
        return None
    s = sorted(values)
    return round(s[min(int(len(s) * 0.9), len(s) - 1)], 1)


def _stddev(values):
    return round(statistics.pstdev(values), 1) if len(values) >= 2 else None


MEDIUM_CONFIDENCE_WINDOW = 30
LOW_CONFIDENCE_WINDOW = 10
HIGH_CONFIDENCE_WINDOW = 100
MIN_ZONE_ROUNDS = 10


def _pct(a, b):
    return f"{round(a / b * 100)}%" if b else "—"


def _fmt(val):
    return f"{val:,}" if val is not None else "—"


def _confusion(zone_rounds: list[dict], level: str, top_n: int = 5) -> list[dict]:
    recent = zone_rounds[-MEDIUM_CONFIDENCE_WINDOW:]
    pairs: dict[tuple, list] = defaultdict(list)
    for r in recent:
        real = (r.get("real_geo") or {}).get(level, "")
        guess = (r.get("guess_geo") or {}).get(level, "")
        d = r.get("distance_km")
        if real and guess and real != guess and d is not None:
            pairs[(real, guess)].append(d)
    rows = []
    for (real, guess), dists in pairs.items():
        freq = len(dists)
        avg_d = round(statistics.mean(dists), 1)
        rows.append(
            {
                "real": real,
                "guess": guess,
                "freq": freq,
                "avg_km": avg_d,
                "impact": round(freq * avg_d),
            }
        )
    return sorted(rows, key=lambda x: -x["impact"])[:top_n]


BOOTSTRAP_SAMPLES = 1000


def _bootstrap_ci(values):
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


def _zone_stats(rounds: list[dict], level: str | None) -> dict[str, dict]:
    groups: dict[str, list] = defaultdict(list)
    for r in rounds:
        key = "_global_" if level is None else ((r.get("real_geo") or {}).get(level, "") or "")
        if key:
            groups[key].append(r)

    result = {}
    for zone, zrounds in groups.items():
        if level is not None and len(zrounds) < MIN_ZONE_ROUNDS:
            continue

        w_high = zrounds[-HIGH_CONFIDENCE_WINDOW:]
        w_low = zrounds[-LOW_CONFIDENCE_WINDOW:]
        w_before = (
            zrounds[-MEDIUM_CONFIDENCE_WINDOW:-LOW_CONFIDENCE_WINDOW]
            if len(zrounds) > LOW_CONFIDENCE_WINDOW
            else []
        )

        def dists(rds):
            return [r["distance_km"] for r in rds if r.get("distance_km") is not None]

        d_high = dists(w_high)
        d_low = dists(w_low)
        d_before = dists(w_before)

        med_high = _median(d_high)
        med_low = _median(d_low)
        med_before = _median(d_before)
        p90_high = _p90(d_high)
        p90_low = _p90(d_low)
        p90_before = _p90(d_before)
        std_high = _stddev(d_high)
        std_low = _stddev(d_low)
        std_before = _stddev(d_before)

        score_high = _dist_to_score(med_high) if med_high is not None else None
        p90_score_high = _dist_to_score(p90_high) if p90_high is not None else None
        std_score_high = (
            _dist_to_score((med_high or 0) + (std_high or 0)) if std_high is not None else None
        )

        ci = _bootstrap_ci(d_high)

        result[zone] = {
            "total": len(zrounds),
            "window": len(w_high),
            "score_high": score_high,
            "level_label": _score_label(score_high) if score_high is not None else "—",
            "level_arrow": _arrow(med_low, med_before, lower_is_better=True),
            "p90_label": _score_label(p90_score_high) if p90_score_high is not None else "—",
            "p90_arrow": _arrow(p90_low, p90_before, lower_is_better=True),
            "p90_now_km": p90_high,
            "cons_label": _score_label(std_score_high) if std_score_high is not None else "—",
            "cons_arrow": _arrow(std_low, std_before, lower_is_better=True),
            "std_now_km": std_high,
            "ci_lo": ci[0] if ci else None,
            "ci_hi": ci[1] if ci else None,
        }

    return result


# ── Typed seam ─────────────────────────────────────────────────────────────


@dataclass
class ZoneStats:
    total: int
    window: int
    score_high: int | None
    level_label: str
    level_arrow: str
    p90_label: str
    p90_arrow: str
    p90_now_km: float | None
    cons_label: str
    cons_arrow: str
    std_now_km: float | None
    ci_lo: float | None
    ci_hi: float | None


@dataclass
class ZoneGroup:
    name: str
    rounds: list[dict]


@dataclass
class AnalysisResult:
    level: str | None
    level_label: str
    zones: dict[str, ZoneStats]


GEO_LEVELS = [
    ("general", "General"),
    ("realm", "Reino"),
    ("continent", "Continente"),
    ("biome", "Bioma"),
    ("country", "País"),
    ("ecoregion", "Ecorregión"),
]


_CHILD_LEVEL = {
    "general": None,
    "realm": "biome",
    "continent": "country",
    "biome": "ecoregion",
    "country": "state",
    "ecoregion": None,
}


def analyze(rounds: list[dict], level: str | None) -> AnalysisResult:
    """Compute analysis for a given geo level and return structured result."""
    level_label = dict(GEO_LEVELS).get(level, level) if level else "General"
    zones_raw = _zone_stats(rounds, level)
    zones = {
        name: ZoneStats(
            total=s["total"],
            window=s["window"],
            score_high=s["score_high"],
            level_label=s["level_label"],
            level_arrow=s["level_arrow"],
            p90_label=s["p90_label"],
            p90_arrow=s["p90_arrow"],
            p90_now_km=s["p90_now_km"],
            cons_label=s["cons_label"],
            cons_arrow=s["cons_arrow"],
            std_now_km=s["std_now_km"],
            ci_lo=s["ci_lo"],
            ci_hi=s["ci_hi"],
        )
        for name, s in zones_raw.items()
    }
    return AnalysisResult(level=level, level_label=level_label, zones=zones)
