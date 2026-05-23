"""Tests for _arrow — trend indicator between two values."""

from core.calculator import _arrow


def test_arrow_is_neutral_when_either_value_is_none():
    """None prev or now → →."""
    # Arrange & Act
    a = _arrow(None, 100)
    b = _arrow(100, None)
    c = _arrow(None, None)

    # Assert
    assert a == "→"
    assert b == "→"
    assert c == "→"


def test_arrow_shows_improvement_when_lower_is_better():
    """lower_is_better=True: lower now_val is improvement → ↑."""
    # Arrange
    prev = 100
    now = 50

    # Act
    result = _arrow(now, prev)

    # Assert
    assert result == "↑"


def test_arrow_shows_decline_when_lower_is_better():
    """lower_is_better=True: higher now_val is decline → ↓."""
    # Arrange
    prev = 50
    now = 100

    # Act
    result = _arrow(now, prev)

    # Assert
    assert result == "↓"


def test_arrow_shows_improvement_when_higher_is_better():
    """lower_is_better=False: higher now_val is improvement → ↑."""
    # Arrange
    prev = 50
    now = 100

    # Act
    result = _arrow(now, prev, lower_is_better=False)

    # Assert
    assert result == "↑"


def test_arrow_shows_decline_when_higher_is_better():
    """lower_is_better=False: lower now_val is decline → ↓."""
    # Arrange
    prev = 100
    now = 50

    # Act
    result = _arrow(now, prev, lower_is_better=False)

    # Assert
    assert result == "↓"


def test_arrow_is_neutral_when_change_is_below_5_percent():
    """Delta < 5% of prev → → regardless of direction."""
    # Arrange & Act
    up_slight = _arrow(104, 100)
    down_slight = _arrow(96, 100)

    # Assert
    assert up_slight == "→"
    assert down_slight == "→"


def test_arrow_is_neutral_when_values_are_equal():
    """Equal values → →."""
    # Arrange
    val = 100

    # Act
    result = _arrow(val, val)

    # Assert
    assert result == "→"


def test_arrow_does_not_crash_when_prev_is_zero():
    """Division by zero guard: prev=0 handled gracefully."""
    # Arrange
    now = 50
    prev = 0

    # Act & Assert (no ZeroDivisionError)
    result = _arrow(now, prev)
    assert result in ("↑", "↓", "→")
