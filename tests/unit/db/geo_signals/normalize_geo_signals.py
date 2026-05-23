"""Tests for normalize_geo_signals — flat DB rows → nested dicts."""

from db.geo_signals import LICENSE_PLATE_SPEC, ROAD_LINE_SPEC, normalize_geo_signals


def test_passes_through_scalar_fields():
    """Scalar fields in spec are copied verbatim from the row."""
    # Arrange
    spec = {"scalars": ["car_type"]}
    row = {"car_type": "normal"}

    # Act
    result = normalize_geo_signals(row, spec)

    # Assert
    assert result == {"car_type": "normal"}


def test_builds_section_from_prefixed_fields():
    """Prefixed row fields are grouped into a nested section dict."""
    # Arrange
    spec = {
        "sections": {
            "inner": {
                "prefix": "inner",
                "fields": ["color", "count"],
            },
        },
    }
    row = {"inner_color": "white", "inner_count": 1}

    # Act
    result = normalize_geo_signals(row, spec)

    # Assert
    assert result == {"inner": {"color": "white", "count": 1}}


def test_omits_section_when_no_fields_match():
    """Section is omitted when none of its prefixed fields exist in row."""
    # Arrange
    spec = {
        "sections": {
            "outer": {
                "prefix": "outer",
                "fields": ["color", "count"],
            },
        },
    }
    row = {"inner_color": "white"}

    # Act
    result = normalize_geo_signals(row, spec)

    # Assert
    assert result == {}


def test_omits_section_with_all_null_fields():
    """Section is omitted when all its fields are null."""
    # Arrange
    spec = {
        "sections": {
            "inner": {
                "prefix": "inner",
                "fields": ["color", "count"],
            },
        },
    }
    row = {"inner_color": None, "inner_count": None}

    # Act
    result = normalize_geo_signals(row, spec)

    # Assert
    assert result == {}


def test_applies_transform_to_scalar():
    """Transforms in spec are applied to scalar values before insertion."""
    # Arrange
    spec = {"scalars": ["rule"], "transforms": {"rule": lambda v: v.replace("_", "-")}}
    row = {"rule": "region_dependant"}

    # Act
    result = normalize_geo_signals(row, spec)

    # Assert
    assert result == {"rule": "region-dependant"}


def test_omits_section_identical_to_reference():
    """Section is dropped when omit_if_identical pairs it with a matching section."""
    # Arrange
    spec = {
        "sections": {
            "front": {"prefix": "front", "fields": ["color"]},
            "back": {"prefix": "back", "fields": ["color"]},
        },
        "omit_if_identical": [["back", "front"]],
    }
    row = {"front_color": "white", "back_color": "white"}

    # Act
    result = normalize_geo_signals(row, spec)

    # Assert
    assert "back" not in result
    assert result["front"] == {"color": "white"}


def test_keeps_section_when_different_from_reference():
    """Section is kept when omit_if_identical sections differ."""
    # Arrange
    spec = {
        "sections": {
            "front": {"prefix": "front", "fields": ["color"]},
            "back": {"prefix": "back", "fields": ["color"]},
        },
        "omit_if_identical": [["back", "front"]],
    }
    row = {"front_color": "white", "back_color": "yellow"}

    # Act
    result = normalize_geo_signals(row, spec)

    # Assert
    assert result["back"] == {"color": "yellow"}


def test_omits_array_in_section_when_all_slots_empty():
    """Array is absent from output when none of its numbered slots have data."""
    # Arrange
    spec = {
        "sections": {
            "front": {
                "prefix": "front",
                "fields": [],
                "arrays": [
                    {
                        "name": "strips",
                        "row_prefix": "strip",
                        "count": 2,
                        "fields": ["color", "side"],
                    },
                ],
            },
        },
    }
    row = {}

    # Act
    result = normalize_geo_signals(row, spec)

    # Assert
    assert result == {}


# ── LICENSE_PLATE_SPEC integration ────────────────────────────────────────────────


def test_license_plate_minimal_front():
    """LICENSE_PLATE_SPEC: front fields only → front section, no strips, no back."""
    # Arrange
    row = {
        "car_type": "normal",
        "front_is_required": True,
        "front_color": "white",
        "front_letter_color": "black",
        "front_shape": "wide",
    }

    # Act
    result = normalize_geo_signals(row, LICENSE_PLATE_SPEC)

    # Assert
    assert result == {
        "car_type": "normal",
        "front": {
            "is_required": True,
            "color": "white",
            "letter_color": "black",
            "shape": "wide",
        },
    }


def test_license_plate_front_with_strip():
    """LICENSE_PLATE_SPEC: front with strip, different back → both sections."""
    # Arrange
    row = {
        "car_type": "normal",
        "front_is_required": True,
        "front_color": "white",
        "front_letter_color": "black",
        "front_shape": "wide",
        "front_strip_color_1": "blue",
        "front_strip_side_1": "left",
        "back_color": "yellow",
        "back_letter_color": "black",
        "back_shape": "wide",
    }

    # Act
    result = normalize_geo_signals(row, LICENSE_PLATE_SPEC)

    # Assert
    assert result == {
        "car_type": "normal",
        "front": {
            "is_required": True,
            "color": "white",
            "letter_color": "black",
            "shape": "wide",
            "strips": [{"color": "blue", "side": "left"}],
        },
        "back": {
            "color": "yellow",
            "letter_color": "black",
            "shape": "wide",
        },
    }


def test_license_plate_electric_omits_back():
    """LICENSE_PLATE_SPEC: no back fields → back section absent."""
    # Arrange
    row = {
        "car_type": "electric",
        "front_is_required": True,
        "front_color": "green",
        "front_letter_color": "white",
        "front_shape": "standard",
    }

    # Act
    result = normalize_geo_signals(row, LICENSE_PLATE_SPEC)

    # Assert
    assert result == {
        "car_type": "electric",
        "front": {
            "is_required": True,
            "color": "green",
            "letter_color": "white",
            "shape": "standard",
        },
    }


def test_license_plate_keeps_back_when_sections_differ():
    """LICENSE_PLATE_SPEC: back kept despite similar values (omit_if_identical checks full dict)."""
    # Arrange
    row = {
        "car_type": "motorcycle",
        "front_is_required": True,
        "front_color": "yellow",
        "front_letter_color": "black",
        "front_shape": "short",
        "back_color": "yellow",
        "back_letter_color": "black",
        "back_shape": "short",
    }

    # Act
    result = normalize_geo_signals(row, LICENSE_PLATE_SPEC)

    # Assert
    assert result == {
        "car_type": "motorcycle",
        "front": {
            "is_required": True,
            "color": "yellow",
            "letter_color": "black",
            "shape": "short",
        },
        "back": {
            "color": "yellow",
            "letter_color": "black",
            "shape": "short",
        },
    }


# ── ROAD_LINE_SPEC integration ────────────────────────────────────────────────────


def test_road_line_rule_only():
    """ROAD_LINE_SPEC: rule scalar only, no sections."""
    # Arrange
    row = {"rule": "whole-country"}

    # Act
    result = normalize_geo_signals(row, ROAD_LINE_SPEC)

    # Assert
    assert result == {"rule": "whole-country"}


def test_road_line_all_sections():
    """ROAD_LINE_SPEC: all three line sections (inner, outer, extra) populated."""
    # Arrange
    row = {
        "rule": "whole-country",
        "inner_color": "white",
        "inner_count": 1,
        "inner_pattern": "dashed",
        "outer_color": "white",
        "outer_count": 1,
        "outer_pattern": "solid",
        "extra_color": "white",
        "extra_pattern": "dashed",
    }

    # Act
    result = normalize_geo_signals(row, ROAD_LINE_SPEC)

    # Assert
    assert result == {
        "rule": "whole-country",
        "inner": {"color": "white", "count": 1, "pattern": "dashed"},
        "outer": {"color": "white", "count": 1, "pattern": "solid"},
        "extra": {"color": "white", "pattern": "dashed"},
    }


def test_road_line_inner_only():
    """ROAD_LINE_SPEC: only inner section present."""
    # Arrange
    row = {
        "rule": "urban",
        "inner_color": "yellow",
        "inner_count": 1,
        "inner_pattern": "solid",
    }

    # Act
    result = normalize_geo_signals(row, ROAD_LINE_SPEC)

    # Assert
    assert result == {
        "rule": "urban",
        "inner": {"color": "yellow", "count": 1, "pattern": "solid"},
    }


def test_road_line_outer_only():
    """ROAD_LINE_SPEC: only outer section present."""
    # Arrange
    row = {
        "rule": "region-dependant",
        "outer_color": "white",
        "outer_count": 1,
        "outer_pattern": "dashed",
    }

    # Act
    result = normalize_geo_signals(row, ROAD_LINE_SPEC)

    # Assert
    assert result == {
        "rule": "region-dependant",
        "outer": {"color": "white", "count": 1, "pattern": "dashed"},
    }


def test_road_line_transforms_rule_underscore():
    """ROAD_LINE_SPEC: underscore in rule value is transformed to hyphen."""
    # Arrange
    row = {
        "rule": "region_dependant",
        "outer_color": "white",
        "outer_count": 1,
        "outer_pattern": "dashed",
    }

    # Act
    result = normalize_geo_signals(row, ROAD_LINE_SPEC)

    # Assert
    assert result == {
        "rule": "region-dependant",
        "outer": {"color": "white", "count": 1, "pattern": "dashed"},
    }
