"""Tests for _bootstrap_ci — bootstrap confidence interval for median."""

import random

from core.calculator import _bootstrap_ci


def test_ci_is_none_when_sample_is_too_small() -> None:
    """Fewer than 20 values → None."""
    # Arrange
    values = [1] * 19

    # Act
    result = _bootstrap_ci(values)

    # Assert
    assert result is None


def test_ci_bounds_are_in_correct_order() -> None:
    """Lower bound is always below upper bound."""
    # Arrange
    random.seed(42)
    values = [random.uniform(100, 500) for _ in range(100)]

    # Act
    random.seed(42)
    ci = _bootstrap_ci(values)

    # Assert
    assert isinstance(ci, tuple)
    assert len(ci) == 2
    assert ci[0] < ci[1]


def test_ci_is_deterministic_with_same_seed() -> None:
    """Seeded RNG produces identical CI on repeated calls."""
    # Arrange
    random.seed(42)
    values = [random.uniform(100, 500) for _ in range(100)]

    # Act
    random.seed(42)
    ci1 = _bootstrap_ci(values)
    random.seed(42)
    ci2 = _bootstrap_ci(values)

    # Assert
    assert ci1 == ci2


def test_ci_is_tight_when_all_values_are_identical() -> None:
    """All values equal → CI equals that value."""
    # Arrange
    values = [50.0] * 100

    # Act
    ci = _bootstrap_ci(values)

    # Assert
    assert ci is not None
    assert ci[0] == ci[1]
