"""zone_stats.py — Per-zone performance statistics for a geo level."""

from collections import defaultdict
from dataclasses import dataclass

from src.analysis.geo_levels import GEO_LEVELS
from src.analysis.scoring import (
    HIGH_CONFIDENCE_WINDOW,
    LOW_CONFIDENCE_WINDOW,
    MEDIUM_CONFIDENCE_WINDOW,
    MIN_ZONE_ROUNDS,
    arrow,
    bootstrap_ci,
    dist_to_score,
    median,
    p90,
    score_label,
    stddev,
)
from src.i18n.lang import translate


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

        med_high = median(d_high)
        med_low = median(d_low)
        med_before = median(d_before)
        p90_high = p90(d_high)
        p90_low = p90(d_low)
        p90_before = p90(d_before)
        std_high = stddev(d_high)
        std_low = stddev(d_low)
        std_before = stddev(d_before)

        score_high = dist_to_score(med_high) if med_high is not None else None
        p90_score_high = dist_to_score(p90_high) if p90_high is not None else None
        std_score_high = (
            dist_to_score((med_high or 0) + (std_high or 0)) if std_high is not None else None
        )

        ci = bootstrap_ci(d_high)

        result[zone] = {
            "total": len(zrounds),
            "window": len(w_high),
            "score_high": score_high,
            "level_label": score_label(score_high) if score_high is not None else "—",
            "level_arrow": arrow(med_low, med_before, lower_is_better=True),
            "p90_label": score_label(p90_score_high) if p90_score_high is not None else "—",
            "p90_arrow": arrow(p90_low, p90_before, lower_is_better=True),
            "p90_now_km": p90_high,
            "cons_label": score_label(std_score_high) if std_score_high is not None else "—",
            "cons_arrow": arrow(std_low, std_before, lower_is_better=True),
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


def analyze(rounds: list[dict], level: str | None) -> AnalysisResult:
    """Compute analysis for a given geo level and return structured result."""
    level_label = translate(dict(GEO_LEVELS).get(level, level) if level else "General")
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
