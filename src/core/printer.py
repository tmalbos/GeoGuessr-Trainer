"""printer.py — ANSI formatting, table layout, and print logic for stats analysis."""

import sys
from typing import TextIO

from src.core.calculator import (
    _CHILD_LEVEL,
    GEO_LEVELS,
    HIGH_CONFIDENCE_WINDOW,
    LOW_CONFIDENCE_WINDOW,
    MEDIUM_CONFIDENCE_WINDOW,
    MIN_ZONE_ROUNDS,
    AnalysisResult,
    _confusion,
)


def print_analysis(
    result: AnalysisResult,
    level: str,
    groups: dict[str, list[dict]],
    stream: TextIO = sys.stdout,
) -> None:
    """Print formatted analysis result to stream."""
    _print_header(result, stream)

    if not result.zones:
        stream.write(f"\n⚠️  No hay zonas con {MIN_ZONE_ROUNDS}+ rondas para este nivel.\n")
        return

    _print_zones(result, level, groups, stream)


def _print_header(result: AnalysisResult, stream: TextIO) -> None:
    label = result.level_label
    stream.write("\n")
    stream.write("═" * 68 + "\n")
    stream.write(f"  ANÁLISIS — {label.upper()}\n")
    stream.write(
        f"  (nivel basado en últ. {HIGH_CONFIDENCE_WINDOW}"
        f" | progreso: últ. {LOW_CONFIDENCE_WINDOW}"
        f" vs últ. {MEDIUM_CONFIDENCE_WINDOW})\n"
    )
    stream.write("═" * 68 + "\n")


def _fmt(val: float | int | None) -> str:
    return f"{val:,}" if val is not None else "—"


def _print_zones(
    result: AnalysisResult,
    level: str,
    groups: dict[str, list[dict]],
    stream: TextIO,
) -> None:
    sorted_zones = sorted(
        result.zones.items(),
        key=lambda x: x[1].score_high if x[1].score_high is not None else 9999,
    )
    label_map = dict(GEO_LEVELS)

    for zone, s in sorted_zones:
        title = "GLOBAL" if zone == "_global_" else zone

        stream.write(f"\n  ▸ {title}  ({s.total} rondas en total)\n")
        stream.write(f"  {'─' * 60}\n")

        ci_str = (
            f"  (rango confiable: {_fmt(s.ci_lo)} – {_fmt(s.ci_hi)} km)"
            if s.ci_lo is not None
            else ""
        )
        stream.write(f"  Nivel actual     : {s.level_label} {s.level_arrow}{ci_str}\n")
        stream.write(
            f"  Peores rondas    : {s.p90_label} {s.p90_arrow}"
            f"  (en tu 10% peor, equivale a {_fmt(s.p90_now_km)} km de error)\n"
        )
        stream.write(
            f"  Consistencia     : {s.cons_label} {s.cons_arrow}"
            f"  (tu variación típica equivale a ±{_fmt(s.std_now_km)} km)\n"
        )

        if level != "general":
            zone_rounds = groups.get(zone, [])
            main_conf = _confusion(zone_rounds, level, top_n=1)
            if main_conf:
                c = main_conf[0]
                stream.write(
                    f"  Confusión clave  : confundís {c['real']} → {c['guess']}"
                    f"  ({c['freq']}x, ~{_fmt(c['avg_km'])} km de penalidad)\n"
                )

            child_level = _CHILD_LEVEL.get(level)
            if child_level:
                child_rounds = [
                    r for r in zone_rounds if (r.get("real_geo") or {}).get(level, "") == zone
                ]
                sec_conf = _confusion(child_rounds, child_level, top_n=3)
                if sec_conf:
                    child_label = label_map.get(child_level, child_level)
                    stream.write(f"  Confusiones ({child_label}):\n")
                    for c in sec_conf:
                        stream.write(
                            f"    • {c['real']} → {c['guess']}"
                            f"  ({c['freq']}x, ~{_fmt(c['avg_km'])} km)\n"
                        )

    stream.write("\n" + "═" * 68 + "\n")
