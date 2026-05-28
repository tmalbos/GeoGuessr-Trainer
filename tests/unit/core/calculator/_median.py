"""Tests for _median — median of a list with rounding."""

from core.calculator import _median


def test_median_is_none_when_list_is_empty() -> None:
    """Empty list → None."""
    # Arrange
    values = []

    # Act
    result = _median(values)

    # Assert
    assert result is None


def test_median_is_middle_value_when_length_is_odd() -> None:
    """Odd-length list → middle element."""
    # Arrange
    values = [1, 3, 5]

    # Act
    result = _median(values)

    # Assert
    assert result == 3.0


def test_median_is_mean_of_two_middle_values_when_length_is_even() -> None:
    """Even-length list → mean of two central values."""
    # Arrange
    values = [1, 2, 3, 10]

    # Act
    result = _median(values)

    # Assert
    assert result == 2.5


def test_median_is_the_element_itself_when_list_has_one_value() -> None:
    """Single element → that element."""
    # Arrange
    values = [42]

    # Act
    result = _median(values)

    # Assert
    assert result == 42.0


def test_median_is_rounded_to_one_decimal() -> None:
    """Result is rounded to 1 decimal place."""
    # Arrange
    values = [1, 2, 3]

    # Act
    result = _median(values)

    # Assert
    assert result == 2.0
