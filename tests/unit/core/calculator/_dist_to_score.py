"""Tests for _dist_to_score — distance → GeoGuessr score conversion."""

import math

from core.calculator import _dist_to_score


def test_score_is_perfect_when_distance_is_zero() -> None:
    """0 km → maximum 5000 points."""
    # Arrange
    km = 0

    # Act
    result = _dist_to_score(km)

    # Assert
    assert result == 5000


def test_score_decreases_as_distance_increases() -> None:
    """Score is monotonic decreasing with distance."""
    # Arrange
    closer = 100
    farther = 200

    # Act
    closer_score = _dist_to_score(closer)
    farther_score = _dist_to_score(farther)

    # Assert
    assert closer_score > farther_score


def test_score_follows_exponential_decay_formula() -> None:
    """Score matches int(5000 * exp(-0.000673 * km) + 0.5)."""
    # Arrange
    km = 500

    # Act
    result = _dist_to_score(km)

    # Assert
    assert result == int(5000 * math.exp(-0.000673 * km) + 0.5)


def test_score_approaches_zero_at_extreme_distance() -> None:
    """20000 km → score below 5."""
    # Arrange
    km = 20000

    # Act
    result = _dist_to_score(km)

    # Assert
    assert result < 5
    assert result >= 0


def test_score_is_rounded_to_nearest_integer() -> None:
    """Result is always an int."""
    # Arrange
    km = 1234

    # Act
    result = _dist_to_score(km)

    # Assert
    assert isinstance(result, int)
