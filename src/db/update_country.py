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
# Helpers
# =============================================================


def normalize_hex_to_enum(hex_color: str) -> str:
    HEX_MAP = {
        "#000000": "black",
        "#FFFFFF": "white",
        "#ffffff": "white",
        "#000080": "blue",
        "#FFFF00": "yellow",
    }
    return HEX_MAP.get(hex_color, hex_color)


def get_country_exists(cur, country_code: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM country
        WHERE code = %s
        """,
        (country_code,),
    )
    return cur.fetchone() is not None


def get_state_id(cur, country_code: str, state_name: str):
    cur.execute(
        """
        SELECT state_id
        FROM state
        WHERE country_code = %s
          AND LOWER(name) = LOWER(%s)
        """,
        (country_code, state_name),
    )
    row = cur.fetchone()
    return row[0] if row else None


def get_biome_id(cur, biome_name: str, realm: str):
    cur.execute(
        """
        SELECT biome_id
        FROM biome
        WHERE LOWER(name) = LOWER(%s)
          AND realm = %s
        """,
        (biome_name, realm),
    )
    row = cur.fetchone()
    return row[0] if row else None


def get_ecoregion_id(cur, ecoregion_name: str, biome_id: int):
    cur.execute(
        """
        SELECT ecoregion_id
        FROM ecoregion
        WHERE LOWER(name) = LOWER(%s)
          AND biome_id = %s
        """,
        (ecoregion_name, biome_id),
    )
    row = cur.fetchone()
    return row[0] if row else None


def build_plate_fields(face: dict | None, prefix: str):
    if face is None:
        return {
            f"{prefix}_color": None,
            f"{prefix}_strip_color_1": None,
            f"{prefix}_strip_side_1": None,
            f"{prefix}_strip_color_2": None,
            f"{prefix}_strip_side_2": None,
            f"{prefix}_letter_color": None,
            f"{prefix}_shape": None,
        }

    color_raw = face.get("color", "")
    if isinstance(color_raw, str) and color_raw.startswith("#"):
        color_raw = normalize_hex_to_enum(color_raw)

    letter_color_raw = face.get("letter_color", "")
    if isinstance(letter_color_raw, str) and letter_color_raw.startswith("#"):
        letter_color_raw = normalize_hex_to_enum(letter_color_raw)

    strips = face.get("strips") or []
    if isinstance(strips, dict):
        strips = [strips]

    strip_1 = strips[0] if len(strips) > 0 else None
    strip_2 = strips[1] if len(strips) > 1 else None

    def parse_strip(strip):
        if not strip:
            return None, None

        strip_color = strip.get("color")
        if isinstance(strip_color, str) and strip_color.startswith("#"):
            strip_color = normalize_hex_to_enum(strip_color)

        return (
            strip_color,
            strip.get("side"),
        )

    strip_1_color, strip_1_side = parse_strip(strip_1)
    strip_2_color, strip_2_side = parse_strip(strip_2)

    return {
        f"{prefix}_color": color_raw,
        f"{prefix}_strip_color_1": strip_1_color,
        f"{prefix}_strip_side_1": strip_1_side,
        f"{prefix}_strip_color_2": strip_2_color,
        f"{prefix}_strip_side_2": strip_2_side,
        f"{prefix}_letter_color": letter_color_raw,
        f"{prefix}_shape": face.get("shape"),
    }


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
# Sync
# =============================================================


def sync_country(cur, country_code: str, country_name: str, data: dict) -> None:
    if not get_country_exists(cur, country_code):
        log.error(f"[{country_code}] Country not found in DB")
        return

    cur.execute(
        """
        DELETE FROM country_issues_license_plate
        WHERE country_code = %s
        """,
        (country_code,),
    )

    cur.execute(
        """
        DELETE FROM country_paints_road_line
        WHERE country_code = %s
        """,
        (country_code,),
    )

    for state in data.get("states", []):
        state_name = state["name"]

        state_id = get_state_id(cur, country_code, state_name)

        if state_id is None:
            log.error(f"[{country_code}] State not found: {state_name}")
            continue

        for terrain in state.get("terrain", []):
            biome_name = terrain.get("biome")
            realm = terrain.get("realm")
            ecoregion_name = terrain.get("ecoregion")

            biome_id = get_biome_id(cur, biome_name, realm)

            if biome_id is None:
                log.error(f"[{country_code}] Biome not found: biome={biome_name}, realm={realm}")
                continue

            ecoregion_id = get_ecoregion_id(cur, ecoregion_name, biome_id)

            if ecoregion_id is None:
                log.error(f"[{country_code}] Ecoregion not found: {ecoregion_name}")
                continue

    for plate in data.get("license_plates", []):
        front = plate.get("front") or {}
        back = plate.get("back")

        front_fields = build_plate_fields(front, "front")
        back_fields = build_plate_fields(back, "back")

        values = {
            "car_type": plate.get("car_type"),
            "front_is_required": front.get("is_required", True),
            **front_fields,
            **back_fields,
        }

        cur.execute(
            """
            SELECT license_plate_id
            FROM license_plate
            WHERE
                car_type IS NOT DISTINCT FROM %(car_type)s
                AND front_is_required IS NOT DISTINCT FROM %(front_is_required)s
                AND front_color IS NOT DISTINCT FROM %(front_color)s
                AND front_strip_color_1 IS NOT DISTINCT FROM %(front_strip_color_1)s
                AND front_strip_side_1 IS NOT DISTINCT FROM %(front_strip_side_1)s
                AND front_strip_color_2 IS NOT DISTINCT FROM %(front_strip_color_2)s
                AND front_strip_side_2 IS NOT DISTINCT FROM %(front_strip_side_2)s
                AND front_letter_color IS NOT DISTINCT FROM %(front_letter_color)s
                AND front_shape IS NOT DISTINCT FROM %(front_shape)s
                AND back_color IS NOT DISTINCT FROM %(back_color)s
                AND back_strip_color_1 IS NOT DISTINCT FROM %(back_strip_color_1)s
                AND back_strip_side_1 IS NOT DISTINCT FROM %(back_strip_side_1)s
                AND back_strip_color_2 IS NOT DISTINCT FROM %(back_strip_color_2)s
                AND back_strip_side_2 IS NOT DISTINCT FROM %(back_strip_side_2)s
                AND back_letter_color IS NOT DISTINCT FROM %(back_letter_color)s
                AND back_shape IS NOT DISTINCT FROM %(back_shape)s
            """,
            values,
        )

        row = cur.fetchone()

        if not row:
            cur.execute(
                """
                INSERT INTO license_plate (
                    car_type,
                    front_is_required,
                    front_color,
                    front_strip_color_1,
                    front_strip_side_1,
                    front_strip_color_2,
                    front_strip_side_2,
                    front_letter_color,
                    front_shape,
                    back_color,
                    back_strip_color_1,
                    back_strip_side_1,
                    back_strip_color_2,
                    back_strip_side_2,
                    back_letter_color,
                    back_shape
                )
                VALUES (
                    %(car_type)s,
                    %(front_is_required)s,
                    %(front_color)s,
                    %(front_strip_color_1)s,
                    %(front_strip_side_1)s,
                    %(front_strip_color_2)s,
                    %(front_strip_side_2)s,
                    %(front_letter_color)s,
                    %(front_shape)s,
                    %(back_color)s,
                    %(back_strip_color_1)s,
                    %(back_strip_side_1)s,
                    %(back_strip_color_2)s,
                    %(back_strip_side_2)s,
                    %(back_letter_color)s,
                    %(back_shape)s
                )
                RETURNING license_plate_id
                """,
                values,
            )
            license_plate_id = cur.fetchone()[0]
        else:
            license_plate_id = row[0]

        cur.execute(
            """
            INSERT INTO country_issues_license_plate (
                country_code,
                license_plate_id
            )
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (country_code, license_plate_id),
        )

    for line in data.get("roads", {}).get("lines", []):
        inner = line.get("inner") or {}
        outer = line.get("outer") or {}
        extra = line.get("extra") or {}

        values = {
            "rule": line.get("rule"),
            "inner_color": inner.get("color"),
            "inner_count": inner.get("count", 0),
            "inner_pattern": inner.get("pattern"),
            "outer_color": outer.get("color"),
            "outer_count": outer.get("count", 0),
            "outer_pattern": outer.get("pattern"),
            "extra_color": extra.get("color"),
            "extra_pattern": extra.get("pattern"),
        }

        cur.execute(
            """
            SELECT road_line_id
            FROM road_line
            WHERE
                rule IS NOT DISTINCT FROM %(rule)s
                AND inner_color IS NOT DISTINCT FROM %(inner_color)s
                AND inner_count IS NOT DISTINCT FROM %(inner_count)s
                AND inner_pattern IS NOT DISTINCT FROM %(inner_pattern)s
                AND outer_color IS NOT DISTINCT FROM %(outer_color)s
                AND outer_count IS NOT DISTINCT FROM %(outer_count)s
                AND outer_pattern IS NOT DISTINCT FROM %(outer_pattern)s
                AND extra_color IS NOT DISTINCT FROM %(extra_color)s
                AND extra_pattern IS NOT DISTINCT FROM %(extra_pattern)s
            """,
            values,
        )

        row = cur.fetchone()

        if not row:
            cur.execute(
                """
                INSERT INTO road_line (
                    rule,
                    inner_color,
                    inner_count,
                    inner_pattern,
                    outer_color,
                    outer_count,
                    outer_pattern,
                    extra_color,
                    extra_pattern
                )
                VALUES (
                    %(rule)s,
                    %(inner_color)s,
                    %(inner_count)s,
                    %(inner_pattern)s,
                    %(outer_color)s,
                    %(outer_count)s,
                    %(outer_pattern)s,
                    %(extra_color)s,
                    %(extra_pattern)s
                )
                RETURNING road_line_id
                """,
                values,
            )
            road_line_id = cur.fetchone()[0]
        else:
            road_line_id = row[0]

        cur.execute(
            """
            INSERT INTO country_paints_road_line (
                country_code,
                road_line_id
            )
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (country_code, road_line_id),
        )


# =============================================================
# Main
# =============================================================


def main():
    conn = psycopg2.connect(os.environ["PG_DSN"])

    with conn.cursor() as cur:
        cur.execute("SELECT code, name FROM country")
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
                sync_country(cur, code, country_name, data)

            conn.commit()
            log.info(f"[{code}] {country_name} — OK")

        except Exception as e:
            conn.rollback()
            log.error(f"[{code}] {country_name} — FAILED: {e}")

    conn.close()


if __name__ == "__main__":
    main()
