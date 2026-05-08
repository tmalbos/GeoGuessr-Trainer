"""
populate.py — Puebla la base de datos geo_signals en MongoDB.

Automático:
  - signs.warning.shape     → regla por región
  - signs.stop.text         → scraping Wikipedia
  - license_plates.front.is_required → scraping Wikipedia
  - terrain                 → shapefile RESOLVE Ecoregions 2017 (requiere descarga manual)

Uso:
  python populate.py                  # todo excepto terrain
  python populate.py --terrain /ruta/al/Ecoregions2017.shp
"""

import argparse
import re
import sys
import time
import yaml
import requests
from bs4 import BeautifulSoup
from pymongo import MongoClient

# ─── config ──────────────────────────────────────────────────────────────────

MONGO_URI = "mongodb://localhost:27017/"
MONGO_DB  = "geoguessr"
MONGO_COL = "geo_signals"

SCHEMA_PATH = "db/schema.yaml"

_session = requests.Session()
_session.headers.update({"User-Agent": "GeoSignals-Populate/1.0"})


# ─── MongoDB ─────────────────────────────────────────────────────────────────

def get_col():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    return client[MONGO_DB][MONGO_COL]


def upsert(col, key: str, data: dict):
    col.update_one(
        {"_id": key},
        {"$set": data},
        upsert=True,
    )


# ─── schema validator ─────────────────────────────────────────────────────────

def load_schema(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def validate_enum(value, allowed: list, field: str):
    if value not in allowed:
        raise ValueError(f"'{value}' no es válido para '{field}'. Valores: {allowed}")


# ─── REST Countries — lista de países ────────────────────────────────────────

def fetch_all_countries() -> list[dict]:
    print("Obteniendo lista de países desde REST Countries...")
    r = _session.get(
        "https://restcountries.com/v3.1/all",
        params={"fields": "cca2,name,car,region,subregion"},
        timeout=15,
    )
    data = r.json()
    print(f"  {len(data)} países encontrados.")
    return data


# ─── warning shape ───────────────────────────────────────────────────────────

# diamond: USA, Canadá, Australia, NZ, Japón, México, Centroamérica, Sudamérica
DIAMOND_CODES = {
    "US","CA","AU","NZ","JP","MX","GT","BZ","HN","SV","NI","CR","PA",
    "AR","BO","BR","CL","CO","EC","GY","PY","PE","SR","UY","VE",
    "CU","DO","HT","JM","TT","BB","LC","VC","GD","AG","DM","KN","BS",
    "KR","TH","MM","LA","KH","MY","ID","PH",
}

def warning_shape(cca2: str) -> str:
    return "diamond" if cca2 in DIAMOND_CODES else "triangle"


# ─── stop sign — Wikipedia scraping ──────────────────────────────────────────

# Mapa manual para países con texto no-latino o conocidos
STOP_TEXT_MANUAL = {
    "JP": "止まれ",
    "CN": "停",
    "TW": "停",
    "TH": "หยุด",
    "KH": "ឈប់",
    "KP": "섯",
    "AM": "ԿԱՆԳ",
    "GE": "STOP",
    "AZ": "STOP",
    "BD": "",      # sin texto
    "NP": "",      # sin texto
    "QA": "قف",
    "SA": "قف",
    "AE": "قف",
    "EG": "قف",
    "IQ": "قف",
    "SY": "قف",
    "JO": "قف",
    "LB": "قف",
    "KW": "قف",
    "BH": "قف",
    "OM": "قف",
    "YE": "قف",
    "LY": "قف",
    "DZ": "قف",
    "MA": "قف",
    "TN": "قف",
    "MX": "ALTO",
    "GT": "ALTO", "BZ": "ALTO", "HN": "ALTO",
    "SV": "ALTO", "NI": "ALTO", "CR": "ALTO", "PA": "ALTO",
    "AR": "PARE", "BO": "PARE", "BR": "PARE", "CL": "PARE",
    "CO": "PARE", "EC": "PARE", "PY": "PARE", "PE": "PARE",
    "UY": "PARE", "VE": "PARE", "CU": "PARE", "DO": "PARE",
    "PR": "PARE",
    "TR": "DUR",
    "ET": "ቁም",
    "VU": "STOP",   # circular, texto STOP
}

STOP_SHAPE_MANUAL = {
    "JP": "triangle-inverted",
    "VU": "circle",
    "CU": "circle",  # Cuba usa señal circular antigua
    "PK": "circle",
}

def stop_sign_data(cca2: str) -> dict:
    text  = STOP_TEXT_MANUAL.get(cca2, "STOP")
    shape = STOP_SHAPE_MANUAL.get(cca2, "octagon")
    return {"text": text, "shape": shape}


# ─── license plate front required ────────────────────────────────────────────

# Países donde la placa delantera NO es requerida
NO_FRONT_PLATE = {
    "US",  # algunos estados
    "AU",  # algunos estados (QLD, SA para motocicletas)
    "NZ",
    "GB",
    "IN",  # motocicletas frecuentemente sin placa delantera
    "PK",
    "MY",
    "SG",
    "MM",
    "BD",
    "LK",
    "AF",
    "SD",
    "ET",
    "KE",   # inconsistente
    "NG",
    "GH",
    "EG",   # inconsistente
}

def front_plate_required(cca2: str) -> bool:
    return cca2 not in NO_FRONT_PLATE


# ─── terrain — ecoregions shapefile ──────────────────────────────────────────

def process_terrain(shapefile_path: str, col):
    try:
        import geopandas as gpd
    except ImportError:
        print("  geopandas no instalado. Corré: pip install geopandas")
        return

    print(f"\nProcesando terrain desde {shapefile_path}...")
    print("  Cargando ecoregiones (puede tardar ~30s)...")
    eco = gpd.read_file(shapefile_path)[["ECO_NAME", "BIOME_NAME", "REALM", "geometry"]]

    print("  Descargando polígonos de países (Natural Earth)...")
    states_url = "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_1_states_provinces.zip"
    states = gpd.read_file(states_url)[["iso_a2", "name", "geometry"]]

    states = states.rename(columns={
        "iso_a2": "country_code",
        "name": "state_name"
    })

    print(f"  Calculando intersecciones para {len(states)} estados...")
    eco = eco.to_crs(epsg=3857)
    states = states.to_crs(epsg=3857)

    country_states = {}
    for _, state in states.iterrows():
        cca2 = state["country_code"]
        state_name = state["state_name"]
        geom = state["geometry"]

        if geom is None or geom.is_empty:
            continue

        # filtrar rápido
        intersection = gpd.clip(eco, geom)
        if intersection.empty:
            continue

        # intersección real (esto es clave)
        intersection["geometry"] = intersection.geometry.intersection(geom)

        # limpiar geometrías inválidas
        intersection = intersection[~intersection.is_empty]
        if intersection.empty:
            continue

        intersection["area"] = intersection.geometry.area
        total_area = intersection["area"].sum()

        if total_area < 1e-6:
            continue

        intersection["pct"] = (intersection["area"] / total_area * 100).round(2)

        terrain_list = (
            intersection.groupby(["ECO_NAME", "BIOME_NAME", "REALM"])["pct"]
                .sum()
                .reset_index()
                .rename(columns={
                    "ECO_NAME": "ecoregion",
                    "BIOME_NAME": "biome",
                    "REALM": "realm",
                    "pct": "coverage_pct"
                })
                .sort_values("coverage_pct", ascending=False)
                .to_dict("records")
        )

        if cca2 not in country_states:
            country_states[cca2] = []

        country_states[cca2].append({
            "name": state_name,
            "terrain": terrain_list
        })

    for cca2, states_list in country_states.items():
        col.update_one(
            {"country_code": cca2},
            {"$set": {"states": states_list}},
            upsert=True
        )

    print("  ✅ Terrain procesado.")


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--terrain", metavar="SHAPEFILE", help="Ruta al archivo Ecoregions2017.shp")
    args = parser.parse_args()

    schema = load_schema(SCHEMA_PATH)
    col    = get_col()

    countries = fetch_all_countries()

    print("\nPoblando datos automáticos...")
    for c in countries:
        cca2 = c.get("cca2", "")
        name = c.get("name", {}).get("common", "")
        if not cca2:
            continue

        data = {
            "country_code": cca2,
            "country_name": name,
            "signs": {
                "warning": {
                    "shape": warning_shape(cca2),
                },
                "stop": {
                    "shape": stop_sign_data(cca2)["shape"],
                    "text": {"value": stop_sign_data(cca2)["text"]},
                },
            },
            "license_plates": {
                "front": {
                    "is_required": front_plate_required(cca2),
                },
            },
        }
        upsert(col, cca2, data)
        print(f"  [{cca2}] {name}")

    print(f"\n✅ {len(countries)} países insertados.")

    if args.terrain:
        process_terrain(args.terrain, col)
    else:
        print("\nℹ️  Para poblar terrain, descargá el shapefile de https://ecoregions.appspot.com")
        print("   y corré: python populate.py --terrain /ruta/a/Ecoregions2017.shp")


if __name__ == "__main__":
    main()