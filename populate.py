"""
populate_pg.py — Puebla country, state, biome y ecoregion directo en PostgreSQL.

Fuente única para nombres geográficos: Overpass API (OSM) — misma fuente que Nominatim.

Dependencias:
    pip install psycopg2-binary python-dotenv requests

Uso:
    python populate_pg.py                                    # countries + states + eco
    python populate_pg.py --no-states                        # omitir states
    python populate_pg.py --no-eco                           # omitir biomas y ecoregiones
    python populate_pg.py --terrain /ruta/Ecoregions2017.shp # ruta al shapefile
"""

import argparse
import os
import time

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

_session = requests.Session()
_session.headers.update({"User-Agent": "GeoSignals-Populate/1.0"})

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


# =============================================================
# Conexión
# =============================================================


def get_pg():
    return psycopg2.connect(os.environ["PG_DSN"])


# =============================================================
# Overpass helpers
# =============================================================


def overpass_query(query: str, retries: int = 3) -> list[dict]:
    for attempt in range(retries):
        try:
            r = _session.post(OVERPASS_URL, data={"data": query}, timeout=60)
            return r.json().get("elements", [])
        except Exception as e:
            print(f"    [WARN] Overpass attempt {attempt + 1} failed: {e}")
            time.sleep(5)
    return []


def get_name(tags: dict) -> str:
    return tags.get("name:en") or tags.get("name", "")


# =============================================================
# Countries
# =============================================================


def fetch_countries() -> list[dict]:
    """Obtiene todos los países (admin_level=2) desde Overpass."""
    print("Fetching countries from Overpass...")
    query = """
[out:json][timeout:60];
relation["admin_level"="2"]["boundary"="administrative"]["ISO3166-1"];
out tags;
"""
    elements = overpass_query(query)
    countries = []
    for el in elements:
        tags = el.get("tags", {})
        code = tags.get("ISO3166-1")
        name = get_name(tags)
        if code and name:
            countries.append({"code": code, "name": name})
    print(f"  {len(countries)} countries found.")
    return countries


def populate_countries(cur, countries: list[dict]) -> None:
    print("Inserting countries...")
    for c in countries:
        cur.execute(
            """
            INSERT INTO country (code, name)
            VALUES (%s, %s)
            ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
            """,
            (c["code"], c["name"]),
        )
    print(f"  {len(countries)} countries inserted.")


# =============================================================
# States
# =============================================================


def fetch_states_for_country(code: str) -> list[str]:
    query = f"""
[out:json][timeout:30];
area["ISO3166-1"="{code}"][admin_level=2];
relation["admin_level"="4"]["boundary"="administrative"](area);
out tags;
"""
    elements = overpass_query(query)
    names = []
    for el in elements:
        name = get_name(el.get("tags", {}))
        if name and name not in names:
            names.append(name)
    return sorted(names)


def populate_states(cur, countries: list[dict]) -> None:
    print("\nInserting states from Overpass (this will take several minutes)...")
    for c in countries:
        code = c["code"]
        name = c["name"]

        states = fetch_states_for_country(code)

        if states:
            for state_name in states:
                cur.execute(
                    """
                    INSERT INTO state (country_code, name)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (code, state_name),
                )
            print(f"  [{code}] {name} — {len(states)} states")
        else:
            print(f"  [{code}] {name} — no level-4 divisions")

        time.sleep(1)


# =============================================================
# Biomes + Ecoregions — Ecoregions2017 shapefile
# =============================================================


def populate_eco(cur, shapefile_path: str) -> None:
    try:
        import geopandas as gpd
    except ImportError:
        print("  geopandas not installed. Run: pip install geopandas")
        return

    print(f"\nLoading ecoregions from {shapefile_path}...")
    eco = gpd.read_file(shapefile_path)[["ECO_NAME", "BIOME_NAME", "REALM"]]
    eco = eco.drop_duplicates(subset=["ECO_NAME"])
    print(f"  {len(eco)} ecoregions found.")

    biome_id_cache = {}

    for _, row in eco.iterrows():
        biome_name = row["BIOME_NAME"]
        realm = row["REALM"]
        eco_name = row["ECO_NAME"]

        if not biome_name or not eco_name or realm == "N/A":
            continue

        key = (biome_name, realm)
        if key not in biome_id_cache:
            cur.execute(
                """
                INSERT INTO biome (name, realm)
                VALUES (%s, %s)
                ON CONFLICT (name, realm) DO NOTHING
                RETURNING biome_id
                """,
                (biome_name, realm),
            )
            row_result = cur.fetchone()
            if row_result:
                biome_id_cache[key] = row_result[0]
            else:
                cur.execute(
                    "SELECT biome_id FROM biome WHERE name = %s AND realm = %s",
                    (biome_name, realm),
                )
                biome_id_cache[key] = cur.fetchone()[0]

        biome_id = biome_id_cache[key]

        cur.execute(
            """
            INSERT INTO ecoregion (biome_id, name)
            VALUES (%s, %s)
            ON CONFLICT (name) DO NOTHING
            """,
            (biome_id, eco_name),
        )

    print("  Biomes and ecoregions inserted.")


# =============================================================
# Main
# =============================================================


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-states", action="store_true", help="Skip states")
    parser.add_argument("--no-eco", action="store_true", help="Skip biomes and ecoregions")
    parser.add_argument("--terrain", metavar="SHAPEFILE", help="Path to Ecoregions2017.shp")
    args = parser.parse_args()

    conn = get_pg()

    try:
        countries = fetch_countries()

        with conn.cursor() as cur:
            populate_countries(cur, countries)
            conn.commit()

            if not args.no_states:
                populate_states(cur, countries)
                conn.commit()
            else:
                print("\nStates skipped (--no-states).")

            if not args.no_eco:
                if args.terrain:
                    populate_eco(cur, args.terrain)
                    conn.commit()
                else:
                    print("\nNo shapefile provided. Run with --terrain /path/to/Ecoregions2017.shp")
            else:
                print("\nEcoregions skipped (--no-eco).")

        print("\nDone.")

    except Exception as e:
        conn.rollback()
        print(f"\nERROR: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
