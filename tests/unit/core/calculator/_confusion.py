"""Tests for _confusion — frequent real-vs-guess mismatches."""

from core.calculator import _confusion


def _make_round(real_country: str, guess_country: str, dist_km: float) -> dict:
    return {
        "real_geo": {"country": real_country},
        "guess_geo": {"country": guess_country},
        "distance_km": dist_km,
    }


def test_confusion_is_empty_when_no_rounds():
    """No rounds → empty list."""
    # Arrange
    rounds = []

    # Act
    result = _confusion(rounds, "country")

    # Assert
    assert result == []


def test_confusion_is_empty_when_all_guesses_match():
    """All real == guess → no confusion entries."""
    # Arrange
    rounds = [_make_round("France", "France", 100) for _ in range(40)]

    # Act
    result = _confusion(rounds, "country")

    # Assert
    assert result == []


def test_confusion_returns_mismatches_sorted_by_impact():
    """Known mismatches appear sorted by impact (freq × avg_km), descending."""
    # Arrange
    rounds = [_make_round("France", "France", 100) for _ in range(40)]
    rounds += [_make_round("France", "Germany", 500) for _ in range(20)]
    rounds += [_make_round("France", "Spain", 300) for _ in range(10)]

    # Act
    result = _confusion(rounds, "country", top_n=5)

    # Assert
    assert len(result) == 2
    assert result[0]["real"] == "France"
    assert result[0]["guess"] == "Germany"
    assert result[0]["freq"] == 20
    assert result[1]["real"] == "France"
    assert result[1]["guess"] == "Spain"


def test_confusion_respects_top_n_limit():
    """top_n parameter caps the number of returned entries."""
    # Arrange
    rounds = [_make_round("France", "France", 100) for _ in range(40)]
    rounds += [_make_round("France", "Germany", 500) for _ in range(20)]
    rounds += [_make_round("France", "Spain", 300) for _ in range(10)]
    rounds += [_make_round("France", "Italy", 200) for _ in range(15)]

    # Act
    result = _confusion(rounds, "country", top_n=2)

    # Assert
    assert len(result) == 2


def test_confusion_skips_rounds_with_missing_geo():
    """Round with no real_geo or guess_geo is silently ignored."""
    # Arrange
    rounds = [
        _make_round("France", "France", 100),
        {"distance_km": 200},  # missing both geo fields
    ]

    # Act
    result = _confusion(rounds, "country")

    # Assert
    assert result == []
