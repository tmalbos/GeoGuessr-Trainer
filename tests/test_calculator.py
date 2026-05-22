"""Tests for calculator.py — pure computation functions."""

import math
import random

from src.core.calculator import (
    AnalysisResult,
    ZoneStats,
    _arrow,
    _bootstrap_ci,
    _confusion,
    _dist_to_score,
    _median,
    _p90,
    _score_label,
    _stddev,
    _zone_stats,
    analyze,
)


def test_dist_to_score_zero_distance():
    """0 km → perfect 5000 points."""
    assert _dist_to_score(0) == 5000


def test_dist_to_score_known_values():
    """Spot-check known distances."""
    # formula: int(5000 * exp(-0.000673 * km) + 0.5)
    expected_500 = int(5000 * math.exp(-0.000673 * 500) + 0.5)
    assert _dist_to_score(500) == expected_500

    expected_1000 = int(5000 * math.exp(-0.000673 * 1000) + 0.5)
    assert _dist_to_score(1000) == expected_1000

    expected_5000 = int(5000 * math.exp(-0.000673 * 5000) + 0.5)
    assert _dist_to_score(5000) == expected_5000


def test_dist_to_score_very_large_distance():
    """Very large distance → approaches 0."""
    assert _dist_to_score(20000) < 5


def test_score_label_boundaries():
    """Boundary values for each label tier."""
    assert _score_label(0) == "Pésimo"
    assert _score_label(2500) == "Pésimo"
    assert _score_label(2501) == "Decente"
    assert _score_label(3750) == "Decente"
    assert _score_label(3751) == "Alto"
    assert _score_label(4375) == "Alto"
    assert _score_label(4376) == "Excepcional"
    assert _score_label(4713) == "Excepcional"
    assert _score_label(4714) == "Elite"
    assert _score_label(4857) == "Elite"
    assert _score_label(4858) == "Inhumano"
    assert _score_label(5000) == "Inhumano"


def test_arrow_none_values():
    """None values → neutral arrow."""
    assert _arrow(None, 100) == "→"
    assert _arrow(100, None) == "→"
    assert _arrow(None, None) == "→"


def test_arrow_lower_is_better():
    """lower_is_better=True: lower now_val is improvement."""
    assert _arrow(50, 100) == "↑"
    assert _arrow(100, 50) == "↓"


def test_arrow_higher_is_better():
    """lower_is_better=False: higher now_val is improvement."""
    assert _arrow(100, 50, lower_is_better=False) == "↑"
    assert _arrow(50, 100, lower_is_better=False) == "↓"


def test_arrow_small_delta():
    """Change less than 5% → neutral."""
    assert _arrow(104, 100) == "→"
    assert _arrow(96, 100) == "→"


def test_arrow_equal_values():
    """Equal values → neutral."""
    assert _arrow(100, 100) == "→"


def test_arrow_zero_prev():
    """Division by zero guard: prev=0 should not crash."""
    _arrow(50, 0)  # just ensure no ZeroDivisionError


def test_median_empty():
    """Empty list → None."""
    assert _median([]) is None


def test_median_odd():
    """Odd-length list → middle value."""
    assert _median([1, 3, 5]) == 3.0


def test_median_even():
    """Even-length list → mean of two middle values."""
    assert _median([1, 2, 3, 10]) == 2.5


def test_median_single():
    """Single element → that element."""
    assert _median([42]) == 42.0


def test_p90_empty():
    """Empty list → None."""
    assert _p90([]) is None


def test_p90_small():
    """Values sorted, pick 90th percentile index."""
    result = _p90([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert result == 10.0


def test_p90_single():
    """Single element → that element."""
    assert _p90([42]) == 42.0


def test_stddev_empty():
    """Less than 2 elements → None."""
    assert _stddev([]) is None
    assert _stddev([1]) is None


def test_stddev_known():
    """Known values → population stddev."""
    result = _stddev([1, 3])
    assert result == 1.0  # pstdev of [1, 3] = 1.0


def test_bootstrap_ci_small_sample():
    """Fewer than 20 values → None."""
    assert _bootstrap_ci([1] * 19) is None


def test_bootstrap_ci_deterministic():
    """Seeded RNG produces deterministic CI."""
    random.seed(42)
    values = [random.uniform(100, 500) for _ in range(100)]
    random.seed(42)
    ci = _bootstrap_ci(values)
    assert isinstance(ci, tuple)
    assert len(ci) == 2
    assert ci[0] < ci[1]  # lower < upper


def _make_round(real_country: str, guess_country: str, dist_km: float) -> dict:
    return {
        "real_geo": {"country": real_country},
        "guess_geo": {"country": guess_country},
        "distance_km": dist_km,
    }


def test_confusion_empty_rounds():
    """No rounds → empty list."""
    assert _confusion([], "country") == []


def test_confusion_no_mismatches():
    """All real == guess → no confusion entries."""
    rounds = [_make_round("France", "France", 100) for _ in range(40)]
    assert _confusion(rounds, "country") == []


def test_confusion_known_pairs():
    """Known mismatches appear sorted by impact."""
    rounds = [_make_round("France", "France", 100) for _ in range(40)]
    rounds += [_make_round("France", "Germany", 500) for _ in range(20)]
    rounds += [_make_round("France", "Spain", 300) for _ in range(10)]
    result = _confusion(rounds, "country", top_n=5)
    assert len(result) == 2
    assert result[0]["real"] == "France"
    assert result[0]["guess"] == "Germany"
    assert result[0]["freq"] == 20
    assert result[1]["real"] == "France"
    assert result[1]["guess"] == "Spain"


def _make_zone_rounds(n: int, country: str, base_dist: float = 200, step: float = 0) -> list[dict]:
    """Create n rounds for a given country with linearly increasing distances."""
    return [
        {
            "real_geo": {"country": country},
            "guess_geo": {"country": country},
            "distance_km": base_dist + i * step,
        }
        for i in range(n)
    ]


def test_zone_stats_below_min_rounds():
    """Zone with fewer than 10 rounds → excluded."""
    rounds = _make_zone_rounds(5, "France")
    assert _zone_stats(rounds, "country") == {}


def test_zone_stats_single_zone():
    """Single zone with enough rounds returns expected stats dict."""
    rounds = _make_zone_rounds(150, "France", base_dist=300, step=1)
    result = _zone_stats(rounds, "country")
    assert "France" in result
    s = result["France"]
    assert s["total"] == 150


def test_analyze_returns_analysis_result():
    """analyze() returns AnalysisResult with ZoneStats dataclasses."""
    rounds = _make_zone_rounds(150, "France", base_dist=300, step=1)
    result = analyze(rounds, "country")
    assert isinstance(result, AnalysisResult)
    assert result.level == "country"
    assert result.level_label == "País"
    assert "France" in result.zones
    zone = result.zones["France"]
    assert isinstance(zone, ZoneStats)
    assert zone.total == 150


def test_analyze_general_level():
    """level=None (general) yields _global_ zone."""
    rounds = _make_zone_rounds(150, "France", base_dist=300, step=1)
    result = analyze(rounds, None)
    assert result.level is None
    assert result.level_label == "General"
    assert "_global_" in result.zones


def test_analyze_multiple_zones():
    """Multiple zones each get their own ZoneStats."""
    rounds = _make_zone_rounds(80, "France", base_dist=300)
    rounds += _make_zone_rounds(80, "Germany", base_dist=500)
    rounds += _make_zone_rounds(5, "Spain", base_dist=100)  # below threshold, excluded
    result = analyze(rounds, "country")
    assert "France" in result.zones
    assert "Germany" in result.zones
    assert "Spain" not in result.zones  # only 5 rounds < MIN_ZONE_ROUNDS
