"""Tests for _score_label — numeric score → human-readable label."""

from core.calculator import _score_label


def test_label_is_pesimo_when_score_is_2500_or_below():
    """Boundary: 0, 2500 → Pésimo."""
    # Arrange & Act
    low = _score_label(0)
    boundary = _score_label(2500)

    # Assert
    assert low == "Pésimo"
    assert boundary == "Pésimo"


def test_label_is_decente_when_score_is_2501_to_3750():
    """Boundary: 2501, 3750 → Decente."""
    # Arrange & Act
    just_above = _score_label(2501)
    upper = _score_label(3750)

    # Assert
    assert just_above == "Decente"
    assert upper == "Decente"


def test_label_is_alto_when_score_is_3751_to_4375():
    """Boundary: 3751, 4375 → Alto."""
    # Arrange & Act
    just_above = _score_label(3751)
    upper = _score_label(4375)

    # Assert
    assert just_above == "Alto"
    assert upper == "Alto"


def test_label_is_excepcional_when_score_is_4376_to_4713():
    """Boundary: 4376, 4713 → Excepcional."""
    # Arrange & Act
    just_above = _score_label(4376)
    upper = _score_label(4713)

    # Assert
    assert just_above == "Excepcional"
    assert upper == "Excepcional"


def test_label_is_elite_when_score_is_4714_to_4857():
    """Boundary: 4714, 4857 → Elite."""
    # Arrange & Act
    just_above = _score_label(4714)
    upper = _score_label(4857)

    # Assert
    assert just_above == "Elite"
    assert upper == "Elite"


def test_label_is_inhumano_when_score_is_4858_or_above():
    """4858, 5000 → Inhumano."""
    # Arrange & Act
    just_above = _score_label(4858)
    perfect = _score_label(5000)

    # Assert
    assert just_above == "Inhumano"
    assert perfect == "Inhumano"
