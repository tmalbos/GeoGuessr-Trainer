"""Tests for _fmt — number formatting helper."""

from core.calculator import _fmt


def test_fmt_formats_integer_with_thousands_separator() -> None:
    """Large integer → comma-separated string."""
    # Arrange
    val = 1234567

    # Act
    result = _fmt(val)

    # Assert
    assert result == "1,234,567"


def test_fmt_formats_float_with_thousands_separator() -> None:
    """Float → comma-separated string."""
    # Arrange
    val = 1234.5

    # Act
    result = _fmt(val)

    # Assert
    assert result == "1,234.5"


def test_fmt_returns_em_dash_when_value_is_none() -> None:
    """None → em dash."""
    # Arrange
    val = None

    # Act
    result = _fmt(val)

    # Assert
    assert result == "—"


def test_fmt_formats_small_number_without_separator() -> None:
    """Small number → plain string, no commas."""
    # Arrange
    val = 42

    # Act
    result = _fmt(val)

    # Assert
    assert result == "42"


def test_fmt_formats_zero() -> None:
    """Zero → '0'."""
    # Arrange
    val = 0

    # Act
    result = _fmt(val)

    # Assert
    assert result == "0"
