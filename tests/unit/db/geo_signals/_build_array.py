"""Tests for _build_array — build an array of sub-objects from numbered row fields."""

from db.geo_signals import _build_array


def test_builds_single_element_from_numbered_fields() -> None:
    """Numbered sub-fields at index 1 → single-element list."""
    # Arrange
    row = {"front_strip_color_1": "blue", "front_strip_side_1": "top"}
    cfg = {"name": "strips", "row_prefix": "strip", "count": 2, "fields": ["color", "side"]}

    # Act
    result = _build_array(row, "front", cfg)

    # Assert
    assert result == [{"color": "blue", "side": "top"}]


def test_builds_multiple_elements() -> None:
    """Multiple populated indices → multi-element list."""
    # Arrange
    row = {
        "front_strip_color_1": "blue",
        "front_strip_side_1": "top",
        "front_strip_color_2": "red",
        "front_strip_side_2": "bottom",
    }
    cfg = {"name": "strips", "row_prefix": "strip", "count": 2, "fields": ["color", "side"]}

    # Act
    result = _build_array(row, "front", cfg)

    # Assert
    assert result == [
        {"color": "blue", "side": "top"},
        {"color": "red", "side": "bottom"},
    ]


def test_skips_empty_slots() -> None:
    """Slots with no populated fields are skipped."""
    # Arrange
    row = {"front_strip_color_1": "blue", "front_strip_side_1": "top"}
    cfg = {"name": "strips", "row_prefix": "strip", "count": 2, "fields": ["color", "side"]}

    # Act
    result = _build_array(row, "front", cfg)

    # Assert
    assert result == [{"color": "blue", "side": "top"}]


def test_returns_none_when_no_elements() -> None:
    """No populated slots at all → None."""
    # Arrange
    row = {}
    cfg = {"name": "strips", "row_prefix": "strip", "count": 2, "fields": ["color", "side"]}

    # Act
    result = _build_array(row, "front", cfg)

    # Assert
    assert result is None


def test_skips_null_fields_in_element() -> None:
    """Within a slot, null-valued fields are excluded."""
    # Arrange
    row = {"front_strip_color_1": "blue", "front_strip_side_1": None}
    cfg = {"name": "strips", "row_prefix": "strip", "count": 1, "fields": ["color", "side"]}

    # Act
    result = _build_array(row, "front", cfg)

    # Assert
    assert result == [{"color": "blue"}]


def test_defaults_row_prefix_to_name() -> None:
    """When row_prefix is omitted, name is used as the row prefix."""
    # Arrange
    row = {"front_strips_color_1": "blue"}
    cfg = {"name": "strips", "count": 1, "fields": ["color"]}

    # Act
    result = _build_array(row, "front", cfg)

    # Assert
    assert result == [{"color": "blue"}]
