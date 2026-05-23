"""Tests for _stddev — population standard deviation with rounding."""

from core.calculator import _stddev


def test_stddev_is_none_when_less_than_two_values():
    """Fewer than 2 elements → None."""
    # Arrange & Act
    empty = _stddev([])
    single = _stddev([1])

    # Assert
    assert empty is None
    assert single is None


def test_stddev_of_two_different_values():
    """pstdev([1, 3]) = 1.0."""
    # Arrange
    values = [1, 3]

    # Act
    result = _stddev(values)

    # Assert
    assert result == 1.0


def test_stddev_is_zero_when_all_values_are_equal():
    """All identical values → 0."""
    # Arrange
    values = [5, 5, 5, 5]

    # Act
    result = _stddev(values)

    # Assert
    assert result == 0.0


def test_stddev_is_rounded_to_one_decimal():
    """Result is rounded to 1 decimal place."""
    # Arrange
    values = [1, 2]

    # Act
    result = _stddev(values)

    # Assert
    assert result == 0.5
