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
from src.i18n.lang import translate


def print_analysis(
    result: AnalysisResult,
    level: str,
    groups: dict[str, list[dict]],
    stream: TextIO = sys.stdout,
) -> None:
    """Print formatted analysis result to stream."""
    _print_header(result, stream)

    if not result.zones:
        stream.write(
            "\n"
            + translate("⚠️  No zones with {n}+ rounds for this level.", n=MIN_ZONE_ROUNDS)
            + "\n"
        )
        return

    _print_zones(result, level, groups, stream)


def _print_header(result: AnalysisResult, stream: TextIO) -> None:
    label = result.level_label
    stream.write("\n")
    stream.write("═" * 68 + "\n")
    stream.write(translate("  ANALYSIS — {label}", label=label.upper()) + "\n")
    stream.write(
        translate(
            "  (level based on last {high} | progress: last {low} vs last {mid})",
            high=HIGH_CONFIDENCE_WINDOW,
            low=LOW_CONFIDENCE_WINDOW,
            mid=MEDIUM_CONFIDENCE_WINDOW,
        )
        + "\n"
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

        stream.write("\n  ▸ " + title + "  (" + translate("{n} rounds total", n=s.total) + ")\n")
        stream.write(f"  {'─' * 60}\n")

        ci_str = (
            "  ("
            + translate("reliable range: {lo} – {hi} km", lo=_fmt(s.ci_lo), hi=_fmt(s.ci_hi))
            + ")"
            if s.ci_lo is not None
            else ""
        )
        stream.write(
            translate(
                "  Current level    : {label} {arrow}", label=s.level_label, arrow=s.level_arrow
            )
            + ci_str
            + "\n"
        )
        stream.write(
            translate("  Worst rounds     : {label} {arrow}", label=s.p90_label, arrow=s.p90_arrow)
            + "  ("
            + translate("in your worst 10%, equivalent to {km} km error", km=_fmt(s.p90_now_km))
            + ")\n"
        )
        stream.write(
            translate(
                "  Consistency      : {label} {arrow}", label=s.cons_label, arrow=s.cons_arrow
            )
            + "  ("
            + translate("your typical variation is ±{km} km", km=_fmt(s.std_now_km))
            + ")\n"
        )

        if level != "general":
            zone_rounds = groups.get(zone, [])
            main_conf = _confusion(zone_rounds, level, top_n=1)
            if main_conf:
                c = main_conf[0]
                stream.write(
                    translate(
                        "  Key confusion    : you mix up {real} → {guess}",
                        real=c["real"],
                        guess=c["guess"],
                    )
                    + "  ("
                    + translate("{freq}x, ~{km} km penalty", freq=c["freq"], km=_fmt(c["avg_km"]))
                    + ")\n"
                )

            child_level = _CHILD_LEVEL.get(level)
            if child_level:
                child_rounds = [
                    r for r in zone_rounds if (r.get("real_geo") or {}).get(level, "") == zone
                ]
                sec_conf = _confusion(child_rounds, child_level, top_n=3)
                if sec_conf:
                    child_label = label_map.get(child_level, child_level)
                    stream.write(translate("  Confusions ({label}):", label=child_label) + "\n")
                    for c in sec_conf:
                        stream.write(
                            f"    • {c['real']} → {c['guess']}"
                            + "  ("
                            + translate("{freq}x, ~{km} km", freq=c["freq"], km=_fmt(c["avg_km"]))
                            + ")\n"
                        )

    stream.write("\n" + "═" * 68 + "\n")
