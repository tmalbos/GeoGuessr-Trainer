"""Tests for _zone_stats — per-zone statistics computation."""

from core.calculator import _zone_stats


def _make_zone_rounds(n: int, country: str, base_dist: float = 200, step: float = 0) -> list[dict]:
    return [
        {
            "real_geo": {"country": country},
            "guess_geo": {"country": country},
            "distance_km": base_dist + i * step,
        }
        for i in range(n)
    ]


def test_zone_is_excluded_when_below_min_rounds():
    """Zone with fewer than 10 rounds → excluded from results."""
    # Arrange
    rounds = _make_zone_rounds(5, "France")

    # Act
    result = _zone_stats(rounds, "country")

    # Assert
    assert result == {}


def test_zone_returns_expected_stats_when_above_threshold():
    """Single zone with enough rounds returns dict with total, score, windows."""
    # Arrange
    rounds = _make_zone_rounds(150, "France", base_dist=300, step=1)

    # Act
    result = _zone_stats(rounds, "country")

    # Assert
    assert "France" in result
    s = result["France"]
    assert s["total"] == 150
    assert s["window"] == 100  # HIGH_CONFIDENCE_WINDOW
    assert "score_high" in s
    assert "level_label" in s
    assert "level_arrow" in s


def test_global_level_returns_global_zone():
    """level=None produces a _global_ zone regardless of country data."""
    # Arrange
    rounds = _make_zone_rounds(150, "France", base_dist=300, step=1)

    # Act
    result = _zone_stats(rounds, None)

    # Assert
    assert "_global_" in result
    assert result["_global_"]["total"] == 150


def test_multiple_zones_each_get_their_own_stats():
    """Each zone with >= 10 rounds has independent stats."""
    # Arrange
    rounds = _make_zone_rounds(80, "France", base_dist=300)
    rounds += _make_zone_rounds(80, "Germany", base_dist=500)
    rounds += _make_zone_rounds(5, "Spain", base_dist=100)

    # Act
    result = _zone_stats(rounds, "country")

    # Assert
    assert "France" in result
    assert "Germany" in result
    assert "Spain" not in result


def test_zone_with_no_distance_data_returns_none_values():
    """Rounds without distance_km produce None stats instead of crashing."""
    # Arrange
    rounds = [
        {"real_geo": {"country": "France"}, "guess_geo": {"country": "France"}} for _ in range(20)
    ]

    # Act
    result = _zone_stats(rounds, "country")

    # Assert
    s = result["France"]
    assert s["score_high"] is None
    assert s["level_label"] == "—"
    assert s["level_arrow"] == "→"
    assert s["p90_now_km"] is None
    assert s["ci_lo"] is None
    assert s["ci_hi"] is None
