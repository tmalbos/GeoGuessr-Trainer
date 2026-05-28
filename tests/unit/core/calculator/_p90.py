"""Tests for _p90 — 90th percentile of a list."""

from core.calculator import _p90


def test_p90_is_none_when_list_is_empty() -> None:
    """Empty list → None."""
    # Arrange
    values = []

    # Act
    result = _p90(values)

    # Assert
    assert result is None


def test_p90_is_highest_value_in_sorted_list() -> None:
    """Sorted 1..10 → 10 (the 90th percentile position)."""
    # Arrange
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

    # Act
    result = _p90(values)

    # Assert
    assert result == 10.0


def test_p90_is_single_value_when_list_has_one_element() -> None:
    """Single element [42] → 42."""
    # Arrange
    values = [42]

    # Act
    result = _p90(values)

    # Assert
    assert result == 42.0


def test_p90_is_rounded_to_one_decimal() -> None:
    """Result is rounded to 1 decimal place."""
    # Arrange
    values = [1.23456]

    # Act
    result = _p90(values)

    # Assert
    assert result == 1.2


def test_p90_does_not_exceed_list_length() -> None:
    """Index is clamped to len(s) - 1 when 90th index would be out of bounds."""
    # Arrange
    values = [10, 20]

    # Act
    result = _p90(values)

    # Assert
    assert result == 20.0
