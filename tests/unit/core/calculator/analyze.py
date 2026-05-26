"""Tests for analyze — top-level analysis entry point."""

from core.calculator import AnalysisResult, ZoneStats, analyze


def _make_zone_rounds(n: int, country: str, base_dist: float = 200, step: float = 0) -> list[dict]:
    return [
        {
            "real_geo": {"country": country},
            "guess_geo": {"country": country},
            "distance_km": base_dist + i * step,
        }
        for i in range(n)
    ]


def test_analyze_returns_analysis_result_with_zone_stats():
    """analyze() returns AnalysisResult populated with ZoneStats dataclasses."""
    # Arrange
    rounds = _make_zone_rounds(150, "France", base_dist=300, step=1)

    # Act
    result = analyze(rounds, "country")

    # Assert
    assert isinstance(result, AnalysisResult)
    assert result.level == "country"
    assert result.level_label == "Country"
    assert "France" in result.zones
    zone = result.zones["France"]
    assert isinstance(zone, ZoneStats)
    assert zone.total == 150


def test_analyze_uses_general_label_when_level_is_none():
    """level=None → level_label "General", zone key is _global_."""
    # Arrange
    rounds = _make_zone_rounds(150, "France", base_dist=300, step=1)

    # Act
    result = analyze(rounds, None)

    # Assert
    assert result.level is None
    assert result.level_label == "General"
    assert "_global_" in result.zones


def test_analyze_excludes_zones_below_threshold():
    """Zones with fewer than 10 rounds are omitted from results."""
    # Arrange
    rounds = _make_zone_rounds(80, "France", base_dist=300)
    rounds += _make_zone_rounds(80, "Germany", base_dist=500)
    rounds += _make_zone_rounds(5, "Spain", base_dist=100)

    # Act
    result = analyze(rounds, "country")

    # Assert
    assert "France" in result.zones
    assert "Germany" in result.zones
    assert "Spain" not in result.zones


def test_analyze_returns_empty_zones_when_no_rounds():
    """Empty rounds list → empty zones dict."""
    # Arrange
    rounds = []

    # Act
    result = analyze(rounds, "country")

    # Assert
    assert isinstance(result, AnalysisResult)
    assert result.zones == {}
