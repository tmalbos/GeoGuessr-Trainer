"""
stats.py — Análisis de performance en GeoGuessr.
DB access, level discovery, and display logic.
"""

from collections import defaultdict

from src.core.calculator import (
    _CHILD_LEVEL,
    GEO_LEVELS,
    HIGH_CONFIDENCE_WINDOW,
    LOW_CONFIDENCE_WINDOW,
    MEDIUM_CONFIDENCE_WINDOW,
    MIN_ZONE_ROUNDS,
    _confusion,
    _fmt,
    _zone_stats,
)
from src.db.db import fetch_all_rounds


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
