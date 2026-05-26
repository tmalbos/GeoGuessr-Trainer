"""Tests for _score_label — numeric score → human-readable label."""

from core.calculator import _score_label


def test_label_is_terrible_when_score_is_2500_or_below():
    """Boundary: 0, 2500 → Terrible."""
    # Arrange & Act
    low = _score_label(0)
    boundary = _score_label(2500)

    # Assert
    assert low == "Terrible"
    assert boundary == "Terrible"


def test_label_is_decent_when_score_is_2501_to_3750():
    """Boundary: 2501, 3750 → Decent."""
    # Arrange & Act
    just_above = _score_label(2501)
    upper = _score_label(3750)

    # Assert
    assert just_above == "Decent"
    assert upper == "Decent"


def test_label_is_high_when_score_is_3751_to_4375():
    """Boundary: 3751, 4375 → High."""
    # Arrange & Act
    just_above = _score_label(3751)
    upper = _score_label(4375)

    # Assert
    assert just_above == "High"
    assert upper == "High"


def test_label_is_exceptional_when_score_is_4376_to_4713():
    """Boundary: 4376, 4713 → Exceptional."""
    # Arrange & Act
    just_above = _score_label(4376)
    upper = _score_label(4713)

    # Assert
    assert just_above == "Exceptional"
    assert upper == "Exceptional"


def test_label_is_elite_when_score_is_4714_to_4857():
    """Boundary: 4714, 4857 → Elite."""
    # Arrange & Act
    just_above = _score_label(4714)
    upper = _score_label(4857)

    # Assert
    assert just_above == "Elite"
    assert upper == "Elite"


def test_label_is_inhuman_when_score_is_4858_or_above():
    """4858, 5000 → Inhuman."""
    # Arrange & Act
    just_above = _score_label(4858)
    perfect = _score_label(5000)

    # Assert
    assert just_above == "Inhuman"
    assert perfect == "Inhuman"
