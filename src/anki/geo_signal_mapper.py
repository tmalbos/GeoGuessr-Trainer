"""Normalize flat DB rows into nested dicts for Anki card generation."""

# ── Concrete specs ──────────────────────────────────────────────────────────────
LICENSE_PLATE_SPEC = {
    "scalars": ["car_type"],
    "sections": {
        "front": {
            "prefix": "front",
            "fields": ["is_required", "color", "letter_color", "shape"],
            "arrays": [
                {"name": "strips", "row_prefix": "strip", "count": 2, "fields": ["color", "side"]},
            ],
        },
        "back": {
            "prefix": "back",
            "fields": ["color", "letter_color", "shape"],
            "arrays": [
                {"name": "strips", "row_prefix": "strip", "count": 2, "fields": ["color", "side"]},
            ],
        },
    },
    "omit_if_identical": [["back", "front"]],
}

ROAD_LINE_SPEC = {
    "scalars": ["rule"],
    "transforms": {"rule": lambda v: v.replace("_", "-")},
    "sections": {
        "inner": {
            "prefix": "inner",
            "fields": ["color", "count", "pattern"],
        },
        "outer": {
            "prefix": "outer",
            "fields": ["color", "count", "pattern"],
        },
        "extra": {
            "prefix": "extra",
            "fields": ["color", "pattern"],
        },
    },
}


# ── Normalizer ──────────────────────────────────────────────────────────────────
def normalize_geo_signals(row: dict, spec: dict) -> dict:
    result = {}

    for key in spec.get("scalars", []):
        value = row[key]
        transform = spec.get("transforms", {}).get(key)

        if transform:
            value = transform(value)

        result[key] = value

    for section_name, cfg in spec.get("sections", {}).items():
        section = _build_section(row, cfg)

        if section:
            result[section_name] = section

    for child_key, ref_key in spec.get("omit_if_identical", []):
        if child_key in result and ref_key in result and result[child_key] == result[ref_key]:
            del result[child_key]

    return result


def _build_section(row: dict, cfg: dict) -> dict | None:
    prefix = cfg["prefix"]
    section = {}

    for field in cfg.get("fields", []):
        value = row.get(f"{prefix}_{field}")

        if value is not None:
            section[field] = value

    for array_cfg in cfg.get("arrays", []):
        elements = _build_array(row, prefix, array_cfg)

        if elements:
            section[array_cfg["name"]] = elements

    return section or None


def _build_array(row: dict, prefix: str, cfg: dict) -> list[dict] | None:
    row_prefix = cfg.get("row_prefix", cfg["name"])
    elements = []

    for i in range(1, cfg["count"] + 1):
        element = {}

        for subfield in cfg["fields"]:
            value = row.get(f"{prefix}_{row_prefix}_{subfield}_{i}")

            if value is not None:
                element[subfield] = value

        if element:
            elements.append(element)

    return elements or None
