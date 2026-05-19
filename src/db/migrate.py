"""
migrate.py
Migración de MongoDB → PostgreSQL para el proyecto GeoGuessr.

Dependencias:
    pip install pymongo psycopg2-binary python-dotenv

Variables de entorno esperadas (.env):
    MONGO_URI=mongodb://localhost:27017
    MONGO_DB=tu_db
    PG_DSN=postgresql://user:password@localhost:5432/geoguessr
"""

import logging
import os
import sys

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values
from pymongo import MongoClient

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# =============================================================
# Conexiones
# =============================================================


def get_mongo():
    client = MongoClient(os.environ["MONGO_URI"])
    return client[os.environ["MONGO_DB"]]


def get_pg():
    return psycopg2.connect(os.environ["PG_DSN"])


# =============================================================
# Helpers
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


def map_val(mapping: dict, value, field: str):
    """Mapea un valor con el diccionario dado. Lanza ValueError si no existe."""
    if value is None:
        return None
    if value not in mapping:
        raise ValueError(f"Valor desconocido para '{field}': {repr(value)}")
    return mapping[value]


def normalize_hex_to_enum(hex_color: str) -> str:
    """
    El JSON de Argentina usa hex (#000000) en vez de nombres.
    Mapeamos los más comunes; extendé según sea necesario.
    """
    HEX_MAP = {
        "#000000": "black",
        "#FFFFFF": "white",
        "#ffffff": "white",
        "#000080": "blue",
        "#FFFF00": "yellow",
    }
    return HEX_MAP.get(hex_color, hex_color)


# =============================================================
# Migración: geo_signals → countries, states, state_terrain,
#            license_plate_faces, license_plates, road_lines
# =============================================================


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


def get_or_create_biome(cur, name: str, realm: str) -> int:
    """Retorna el id del bioma, creándolo si no existe."""
    cur.execute("SELECT id FROM biomes WHERE name = %s AND realm = %s", (name, realm))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO biomes (name, realm) VALUES (%s, %s) RETURNING id",
        (name, realm),
    )
    return cur.fetchone()[0]


def get_or_create_ecoregion(cur, name: str, biome_id: int) -> int:
    """Retorna el id de la ecoregión, creándola si no existe."""
    cur.execute("SELECT id FROM ecoregions WHERE name = %s AND biome_id = %s", (name, biome_id))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO ecoregions (name, biome_id) VALUES (%s, %s) RETURNING id",
        (name, biome_id),
    )
    return cur.fetchone()[0]


def migrate_geo_signals(mongo_db, pg_conn):
    log.info("Migrando geo_signals...")
    collection = mongo_db["geo_signals"]
    documents = list(collection.find())
    log.info(f"  Encontrados {len(documents)} documentos")

    with pg_conn.cursor() as cur:
        for doc in documents:
            country_code = doc.get("country_code") or doc.get("_id")
            country_name = doc.get("country_name", country_code)

            # --- country ---
            cur.execute(
                """
                INSERT INTO countries (code, name)
                VALUES (%s, %s)
                ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
                """,
                (country_code, country_name),
            )

            # --- states + ecoregions ---
            for state in doc.get("states", []):
                cur.execute(
                    """
                    INSERT INTO states (country_id, name)
                    VALUES (%s, %s)
                    RETURNING id
                    """,
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

            # --- license_plates ---
            for plate in doc.get("license_plates", []):
                front_id = insert_license_plate_face(
                    cur,
                    plate.get("front"),
                    is_required=plate.get("front", {}).get("is_required", True),
                )
                back_id = insert_license_plate_face(cur, plate.get("back"))

                cur.execute(
                    """
                    INSERT INTO license_plates (country_id, car_type, front_id, back_id)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        country_code,
                        map_val(
                            {
                                t: t
                                for t in [
                                    "normal",
                                    "commercial",
                                    "taxi",
                                    "motorcycle",
                                    "military",
                                    "government",
                                    "electric",
                                ]
                            },
                            plate.get("car_type"),
                            "car_type",
                        ),
                        front_id,
                        back_id,
                    ),
                )

            # --- road_lines ---
            for line in doc.get("roads", {}).get("lines", []):
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
                        map_val(LINE_COLOR_MAP, extra.get("color"), "inner_extra_color")
                        if extra
                        else None,
                        map_val(LINE_PATTERN_MAP, extra.get("pattern"), "inner_extra_pattern")
                        if extra
                        else None,
                        map_val(LINE_COLOR_MAP, outer.get("color"), "outer_color"),
                        outer.get("count") if outer else 0,
                        map_val(LINE_PATTERN_MAP, outer.get("pattern"), "outer_pattern"),
                    ),
                )

        pg_conn.commit()
    log.info("  geo_signals migrado OK")


# =============================================================
# Migración: games
# =============================================================


def migrate_games(mongo_db, pg_conn):
    log.info("Migrando games...")
    games = list(mongo_db["games"].find())
    log.info(f"  Encontrados {len(games)} documentos")

    rows = [
        (
            g["game_id"],
            g.get("map_name"),
            g.get("played_at"),
            g.get("avg_distance"),
            g.get("total_score"),
            g.get("challenge_token"),
            g.get("is_daily", False),
        )
        for g in games
    ]

    with pg_conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO games
                (game_id, map_name, played_at, avg_distance, total_score, challenge_token, is_daily)
            VALUES %s
            ON CONFLICT (game_id) DO NOTHING
            """,
            rows,
        )
        pg_conn.commit()
    log.info("  games migrado OK")


# =============================================================
# Migración: rounds
# =============================================================


def resolve_ecoregion_id(cur, country_name: str, state_name: str, ecoregion_name: str) -> int:
    """Resuelve country+state+ecoregion a un ecoregion_id. Lanza si no existe."""
    cur.execute(
        """
        SELECT e.id
        FROM ecoregions e
        JOIN state_ecoregions se ON se.ecoregion_id = e.id
        JOIN states s ON s.id = se.state_id
        JOIN countries c ON c.code = s.country_id
        WHERE c.name = %s AND s.name = %s AND e.name = %s
        LIMIT 1
        """,
        (country_name, state_name, ecoregion_name),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(
            f"ecoregion no encontrada: country={repr(country_name)} "
            f"state={repr(state_name)} ecoregion={repr(ecoregion_name)}"
        )
    return row[0]


def insert_geo_point(cur, geo: dict) -> int:
    """Inserta un geo_point y devuelve su id."""
    ecoregion_id = resolve_ecoregion_id(
        cur,
        geo.get("country", ""),
        geo.get("state", ""),
        geo.get("ecoregion", ""),
    )

    cur.execute(
        """
        INSERT INTO geo_points (lat, lng, city, ecoregion_id)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (
            geo.get("lat"),
            geo.get("lng"),
            geo.get("city"),
            ecoregion_id,
        ),
    )
    return cur.fetchone()[0]


def migrate_rounds(mongo_db, pg_conn):
    log.info("Migrando rounds...")
    rounds = list(mongo_db["rounds"].find())
    log.info(f"  Encontrados {len(rounds)} documentos")

    with pg_conn.cursor() as cur:
        for r in rounds:
            guess_id = insert_geo_point(cur, r.get("guess_geo", {}))
            real_id = insert_geo_point(cur, r.get("real_geo", {}))

            cur.execute(
                """
                INSERT INTO rounds
                    (game_id, round_number, score, distance_km, steps, time_sec, guess_geo_id, real_geo_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    r["game_id"],
                    r.get("round_number"),
                    r.get("score"),
                    r.get("distance_km"),
                    r.get("steps"),
                    r.get("time_sec"),
                    guess_id,
                    real_id,
                ),
            )

        pg_conn.commit()
    log.info("  rounds migrado OK")


# =============================================================
# Validación post-migración
# =============================================================


def validate(mongo_db, pg_conn):
    log.info("Validando migración...")

    checks = {
        "countries": ("geo_signals", "countries"),
        "games": ("games", "games"),
        "rounds": ("rounds", "rounds"),
    }

    with pg_conn.cursor() as cur:
        for label, (mongo_col, pg_table) in checks.items():
            mongo_count = mongo_db[mongo_col].count_documents({})
            cur.execute(f"SELECT COUNT(*) FROM {pg_table}")
            pg_count = cur.fetchone()[0]

            if mongo_count == pg_count:
                log.info(f"  ✓ {label}: {pg_count} filas")
            else:
                log.warning(f"  ✗ {label}: Mongo={mongo_count} vs PG={pg_count}")


# =============================================================
# Entry point
# =============================================================


def main():
    mongo_db = get_mongo()
    pg_conn = get_pg()

    try:
        migrate_geo_signals(mongo_db, pg_conn)
        migrate_games(mongo_db, pg_conn)
        migrate_rounds(mongo_db, pg_conn)
        validate(mongo_db, pg_conn)
    except Exception as e:
        pg_conn.rollback()
        log.error(f"Error durante la migración: {e}")
        sys.exit(1)
    finally:
        pg_conn.close()

    log.info("Migración completa.")


if __name__ == "__main__":
    main()
