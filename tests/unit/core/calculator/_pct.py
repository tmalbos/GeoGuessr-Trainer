"""Tests for _pct — percentage formatting helper."""

from core.calculator import _pct


def test_pct_formats_ratio_as_percentage() -> None:
    """Normal division → rounded percentage with % suffix."""
    # Arrange
    a, b = 25, 100

    # Act
    result = _pct(a, b)

    # Assert
    assert result == "25%"


def test_pct_rounds_to_nearest_integer() -> None:
    """Fractional results are rounded."""
    # Arrange & Act
    result = _pct(1, 3)

    # Assert
    assert result == "33%"


def test_pct_returns_em_dash_when_denominator_is_zero() -> None:
    """b=0 → em dash (no ZeroDivisionError)."""
    # Arrange
    a, b = 50, 0

    # Act
    result = _pct(a, b)

    # Assert
    assert result == "—"


def test_pct_returns_zero_percent_when_numerator_is_zero() -> None:
    """a=0 → 0%."""
    # Arrange
    a, b = 0, 100

    # Act
    result = _pct(a, b)

    # Assert
    assert result == "0%"
