"""
stats.py — Análisis de performance en GeoGuessr.
"""

import math
import random
import statistics
from collections import defaultdict

from src.db.db import fetch_all_rounds

MIN_ZONE_ROUNDS = 10
BOOTSTRAP_SAMPLES = 1000
LOW_CONFIDENCE_WINDOW = 10
MEDIUM_CONFIDENCE_WINDOW = 30
HIGH_CONFIDENCE_WINDOW = 100

GEO_LEVELS = [
    ("general", "General"),
    ("hemisphere", "Hemisferio"),
    ("realm", "Reino"),
    ("continent", "Continente"),
    ("biome", "Bioma"),
    ("subregion", "Subcontinente"),
    ("country", "País"),
    ("ecoregion", "Ecorregión"),
]

_CHILD_LEVEL = {
    "general": None,
    "hemisphere": "continent",
    "realm": "biome",
    "continent": "subregion",
    "biome": "ecoregion",
    "subregion": "country",
    "country": None,
    "ecoregion": None,
}


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


def _pct(a, b):
    return f"{round(a / b * 100)}%" if b else "—"


def _fmt(val):
    return f"{val:,}" if val is not None else "—"


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


async def _load_rounds() -> list[dict]:
    return await fetch_all_rounds()


async def available_levels(min_rounds: int) -> list[tuple]:
    rounds = await _load_rounds()
    if not rounds:
        return []
    total = len(rounds)
    result = []
    for level, label in GEO_LEVELS:
        if level == "general":
            if total >= min_rounds:
                result.append((level, label, total))
        else:
            counts: dict[str, int] = defaultdict(int)
            for r in rounds:
                zone = (r.get("real_geo") or {}).get(level, "")
                if zone:
                    counts[zone] += 1
            if any(v >= min_rounds for v in counts.values()):
                result.append((level, label, total))
    return result


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


async def print_analysis(level: str):
    rounds = await _load_rounds()
    if not rounds:
        print("\n⚠️  Sin datos.")
        return

    geo_level = None if level == "general" else level
    child_level = _CHILD_LEVEL.get(level)

    groups: dict[str, list] = defaultdict(list)
    for r in rounds:
        key = (
            "_global_"
            if geo_level is None
            else ((r.get("real_geo") or {}).get(geo_level, "") or "")
        )
        if key:
            groups[key].append(r)

    zones = _zone_stats(rounds, geo_level)

    if not zones:
        print(f"\n⚠️  No hay zonas con {MIN_ZONE_ROUNDS}+ rondas para este nivel.")
        return

    label_map = dict(GEO_LEVELS)
    label = label_map.get(level, level)

    print("\n" + "═" * 68)
    print(f"  ANÁLISIS — {label.upper()}")
    print(
        f"  (nivel basado en últ. {HIGH_CONFIDENCE_WINDOW} | progreso: últ. {LOW_CONFIDENCE_WINDOW} vs últ. {MEDIUM_CONFIDENCE_WINDOW})"
    )
    print("═" * 68)

    sorted_zones = sorted(
        zones.items(), key=lambda x: x[1]["score_high"] if x[1]["score_high"] is not None else 9999
    )

    for zone, s in sorted_zones:
        title = "GLOBAL" if zone == "_global_" else zone
        zone_rounds = groups.get(zone, [])

        print(f"\n  ▸ {title}  ({s['total']} rondas en total)")
        print(f"  {'─' * 60}")

        ci_str = (
            f"  (rango confiable: {_fmt(s['ci_lo'])} – {_fmt(s['ci_hi'])} km)"
            if s["ci_lo"] is not None
            else ""
        )
        print(f"  Nivel actual     : {s['level_label']} {s['level_arrow']}{ci_str}")
        print(
            f"  Peores rondas    : {s['p90_label']} {s['p90_arrow']}  (en tu 10% peor, equivale a {_fmt(s['p90_now_km'])} km de error)"
        )
        print(
            f"  Consistencia     : {s['cons_label']} {s['cons_arrow']}  (tu variación típica equivale a ±{_fmt(s['std_now_km'])} km)"
        )

        if geo_level:
            main_conf = _confusion(zone_rounds, geo_level, top_n=1)
            if main_conf:
                c = main_conf[0]
                print(
                    f"  Confusión clave  : confundís {c['real']} → {c['guess']}  ({c['freq']}x, ~{_fmt(c['avg_km'])} km de penalidad)"
                )

        if child_level and geo_level:
            child_rounds = [
                r for r in zone_rounds if (r.get("real_geo") or {}).get(geo_level, "") == zone
            ]
            sec_conf = _confusion(child_rounds, child_level, top_n=3)
            if sec_conf:
                child_label = label_map.get(child_level, child_level)
                print(f"  Confusiones ({child_label}):")
                for c in sec_conf:
                    print(
                        f"    • {c['real']} → {c['guess']}  ({c['freq']}x, ~{_fmt(c['avg_km'])} km)"
                    )

    print("\n" + "═" * 68 + "\n")
