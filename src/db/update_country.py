"""
update_geo_signals.py
Sincroniza los YAMLs de src/db/data con PostgreSQL.

Dependencias:
    pip install psycopg2-binary python-dotenv pyyaml

Uso:
    python update_geo_signals.py          # sincroniza todos los YAMLs
    python update_geo_signals.py AR UY    # sincroniza países específicos por código
"""

import logging
import os
import re
import sys
import unicodedata
from pathlib import Path

import psycopg2
import yaml
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path("./src/db/data")


# =============================================================
# Maps
# =============================================================

ROAD_RULE_MAP = {
    "whole-country": "whole_country",
    "region-dependant": "region_dependant",
    "urban": "urban",
    "rural": "rural",
}

LINE_COLOR_MAP = {
    "white": "white",
    "faded white": "faded_white",
    "yellow": "yellow",
    "orange-tinted yellow": "orange_tinted_yellow",
    "green": "green",
    "red": "red",
    "orange": "orange",
}

LINE_PATTERN_MAP = {
    "solid": "solid",
    "dashed": "dashed",
    "short-dashed": "short_dashed",
    "squares": "squares",
}

PLATE_COLOR_MAP = {
    "white": "white",
    "yellow": "yellow",
    "pastel yellow": "pastel_yellow",
    "blue": "blue",
    "green": "green",
    "orange": "orange",
    "brown": "brown",
    "red": "red",
    "black": "black",
}

PLATE_SHAPE_MAP = {
    "wide": "wide",
    "short": "short",
    "tall": "tall",
    "standard": "standard",
}

STRIP_SIDE_MAP = {
    "left": "left",
    "right": "right",
    "top": "top",
    "bottom": "bottom",
}

CAR_TYPE_VALUES = {
    "normal",
    "commercial",
    "taxi",
    "motorcycle",
    "military",
    "government",
    "electric",
}


# =============================================================
# Helpers
# =============================================================


def map_val(mapping: dict, value, field: str):
    if value is None:
        return None
    if value not in mapping:
        raise ValueError(f"Unknown value for '{field}': {repr(value)}")
    return mapping[value]


def normalize_hex_to_enum(hex_color: str) -> str:
    HEX_MAP = {
        "#000000": "black",
        "#FFFFFF": "white",
        "#ffffff": "white",
        "#000080": "blue",
        "#FFFF00": "yellow",
    }
    return HEX_MAP.get(hex_color, hex_color)


def get_or_create_biome(cur, name: str, realm: str) -> int:
    cur.execute("SELECT id FROM biomes WHERE name = %s AND realm = %s", (name, realm))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO biomes (name, realm) VALUES (%s, %s) RETURNING id", (name, realm))
    return cur.fetchone()[0]


def get_or_create_ecoregion(cur, name: str, biome_id: int) -> int:
    cur.execute("SELECT id FROM ecoregions WHERE name = %s AND biome_id = %s", (name, biome_id))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO ecoregions (name, biome_id) VALUES (%s, %s) RETURNING id", (name, biome_id)
    )
    return cur.fetchone()[0]


def insert_license_plate_face(cur, face: dict | None, is_required: bool = True) -> int | None:
    if face is None:
        return None

    color_raw = face.get("color", "")
    if color_raw.startswith("#"):
        color_raw = normalize_hex_to_enum(color_raw)

    letter_color_raw = face.get("letter_color", "")
    if letter_color_raw.startswith("#"):
        letter_color_raw = normalize_hex_to_enum(letter_color_raw)

    cur.execute(
        """
        INSERT INTO license_plate_faces
            (is_required, color, letter_color, shape)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (
            is_required,
            map_val(PLATE_COLOR_MAP, color_raw, "plate color"),
            map_val(PLATE_COLOR_MAP, letter_color_raw, "letter_color"),
            map_val(PLATE_SHAPE_MAP, face.get("shape"), "plate shape"),
        ),
    )
    face_id = cur.fetchone()[0]

    strips = face.get("strips") or []
    if isinstance(strips, dict):
        strips = [strips]

    for strip in strips:
        strip_color_raw = strip.get("color", "")
        if strip_color_raw.startswith("#"):
            strip_color_raw = normalize_hex_to_enum(strip_color_raw)
        cur.execute(
            "INSERT INTO license_plate_face_strips (face_id, color, side) VALUES (%s, %s, %s)",
            (
                face_id,
                map_val(PLATE_COLOR_MAP, strip_color_raw, "strip color"),
                map_val(STRIP_SIDE_MAP, strip.get("side"), "strip side"),
            ),
        )

    return face_id


# =============================================================
# Name helpers
# =============================================================


def _remove_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _pascal(name: str) -> str:
    cleaned = _remove_accents(name.strip())
    return "".join(
        word.capitalize()
        for word in re.split(r"[\s\-_]+", cleaned)
        if word.lower() not in ("y", "and")
    )


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _remove_accents(name).lower())


# =============================================================
# Upsert
# =============================================================


def upsert_country(cur, country_code: str, country_name: str, data: dict) -> None:
    cur.execute(
        """
        INSERT INTO countries (code, name)
        VALUES (%s, %s)
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
        """,
        (country_code, country_name),
    )

    cur.execute("DELETE FROM states WHERE country_id = %s", (country_code,))

    for state in data.get("states", []):
        cur.execute(
            "INSERT INTO states (country_id, name) VALUES (%s, %s) RETURNING id",
            (country_code, state["name"]),
        )
        state_id = cur.fetchone()[0]

        for t in state.get("terrain", []):
            biome_id = get_or_create_biome(cur, t.get("biome"), t.get("realm"))
            ecoregion_id = get_or_create_ecoregion(cur, t.get("ecoregion"), biome_id)
            cur.execute(
                """
                INSERT INTO state_ecoregions (state_id, ecoregion_id, coverage_pct)
                VALUES (%s, %s, %s)
                ON CONFLICT (state_id, ecoregion_id) DO NOTHING
                """,
                (state_id, ecoregion_id, t.get("coverage_pct")),
            )

    cur.execute("DELETE FROM license_plates WHERE country_id = %s", (country_code,))
    for plate in data.get("license_plates", []):
        front_id = insert_license_plate_face(
            cur,
            plate.get("front"),
            is_required=plate.get("front", {}).get("is_required", True),
        )
        back_id = insert_license_plate_face(cur, plate.get("back"))
        cur.execute(
            "INSERT INTO license_plates (country_id, car_type, front_id, back_id) VALUES (%s, %s, %s, %s)",
            (
                country_code,
                map_val({t: t for t in CAR_TYPE_VALUES}, plate.get("car_type"), "car_type"),
                front_id,
                back_id,
            ),
        )

    cur.execute("DELETE FROM road_lines WHERE country_id = %s", (country_code,))
    for line in data.get("roads", {}).get("lines", []):
        inner = line.get("inner") or {}
        outer = line.get("outer") or {}
        extra = inner.get("extra") or {}
        cur.execute(
            """
            INSERT INTO road_lines (
                country_id, rule,
                inner_color, inner_count, inner_pattern,
                inner_extra_color, inner_extra_pattern,
                outer_color, outer_count, outer_pattern
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                country_code,
                map_val(ROAD_RULE_MAP, line.get("rule"), "road_rule"),
                map_val(LINE_COLOR_MAP, inner.get("color"), "inner_color"),
                inner.get("count") if inner else 0,
                map_val(LINE_PATTERN_MAP, inner.get("pattern"), "inner_pattern"),
                map_val(LINE_COLOR_MAP, extra.get("color"), "inner_extra_color") if extra else None,
                map_val(LINE_PATTERN_MAP, extra.get("pattern"), "inner_extra_pattern")
                if extra
                else None,
                map_val(LINE_COLOR_MAP, outer.get("color"), "outer_color"),
                outer.get("count") if outer else 0,
                map_val(LINE_PATTERN_MAP, outer.get("pattern"), "outer_pattern"),
            ),
        )


# =============================================================
# Main
# =============================================================


def main():
    conn = psycopg2.connect(os.environ["PG_DSN"])

    with conn.cursor() as cur:
        cur.execute("SELECT code, name FROM countries")
        pg_countries = dict(cur.fetchall())
        name_to_code = {_normalize_name(name): code for code, name in pg_countries.items()}

    if len(sys.argv) > 1:
        targets = [c.upper().lstrip(".") for c in sys.argv[1:]]
    else:
        targets = []
        for yaml_path in sorted(DATA_DIR.glob("*.yaml")):
            code = name_to_code.get(_normalize_name(yaml_path.stem))
            if not code:
                log.error(f"No country found for YAML: {yaml_path.name}")
                continue
            targets.append(code)

    for code in targets:
        country_name = pg_countries.get(code)
        if not country_name:
            log.error(f"Country code not in DB: {code}")
            continue

        yaml_path = DATA_DIR / f"{_pascal(country_name)}.yaml"
        if not yaml_path.exists():
            log.error(f"YAML not found for {code}: {yaml_path}")
            continue

        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            with conn.cursor() as cur:
                upsert_country(cur, code, country_name, data)
            conn.commit()
            log.info(f"[{code}] {country_name} — OK")
        except Exception as e:
            conn.rollback()
            log.error(f"[{code}] {country_name} — FAILED: {e}")

    conn.close()


if __name__ == "__main__":
    main()
