"""Tests for _build_section — build a section dict from prefixed row fields."""

from db.geo_signals import _build_section


def test_builds_dict_from_prefixed_fields():
    """Prefixed fields in row → section dict with unprefixed keys."""
    # Arrange
    cfg = {"prefix": "inner", "fields": ["color", "count"]}
    row = {"inner_color": "white", "inner_count": 1}

    # Act
    result = _build_section(row, cfg)

    # Assert
    assert result == {"color": "white", "count": 1}


def test_returns_none_when_no_fields_match():
    """No matching prefixed fields → None."""
    # Arrange
    cfg = {"prefix": "outer", "fields": ["color"]}
    row = {"inner_color": "white"}

    # Act
    result = _build_section(row, cfg)

    # Assert
    assert result is None


def test_skips_null_fields():
    """Fields with None values are omitted from the section dict."""
    # Arrange
    cfg = {"prefix": "inner", "fields": ["color", "count"]}
    row = {"inner_color": "white", "inner_count": None}

    # Act
    result = _build_section(row, cfg)

    # Assert
    assert result == {"color": "white"}


def test_handles_empty_fields_list():
    """No fields and no arrays → None."""
    # Arrange
    cfg = {"prefix": "inner", "fields": []}
    row = {"inner_color": "white"}

    # Act
    result = _build_section(row, cfg)

    # Assert
    assert result is None
