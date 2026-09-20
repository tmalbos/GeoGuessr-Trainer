#!/usr/bin/env python3
"""merge_routes.py.

Filtra vias principales y mergea fragmentos por ruta (ref) en un geojson
de rutas OSM.

No toca rotondas (queda para una segunda pasada).

Uso:
    python merge_routes.py --input input.geojson --output output.geojson [--road-name N]

Requiere:
    pip install geopandas shapely pandas --break-system-packages
"""

import argparse
import warnings

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge, unary_union

# ---------------------------------------------------------------------
# Configuracion (unicos valores que deberias tocar entre paises)
# ---------------------------------------------------------------------

# Clases de via consideradas "rutas principales". Ajustable segun el pais.
MAIN_ROAD_CLASSES = {"motorway", "trunk", "primary", "road"}

# Columnas candidatas, en orden de preferencia. El script usa la primera
# que exista en el geojson, asi no depende de que todos los paises usen
# el mismo esquema de OSM/ogr2ogr.
CLASS_COLUMN_CANDIDATES = ["route", "fclass", "highway", "road_type", "type"]
REF_COLUMN_CANDIDATES = ["ref", "nat_ref", "route_ref"]
NAME_COLUMN_CANDIDATES = ["name", "nombre"]


def find_column(gdf, candidates):
    for c in candidates:
        if c in gdf.columns:
            return c
    return None


def filter_main_roads(gdf):
    class_col = find_column(gdf, CLASS_COLUMN_CANDIDATES)
    if class_col is None:
        warnings.warn(
            "No se encontro columna de clase de via -> no se filtra por tipo.", stacklevel=2
        )
        return gdf
    mask = gdf[class_col].isin(MAIN_ROAD_CLASSES)
    filtered = gdf[mask].copy()
    print(f"[filter] columna usada: '{class_col}' -> {len(filtered)}/{len(gdf)} features")
    return filtered


def get_group_key(gdf):
    """Clave de agrupacion: (ref, name), con fallbacks si faltan."""
    ref_col = find_column(gdf, REF_COLUMN_CANDIDATES)
    name_col = find_column(gdf, NAME_COLUMN_CANDIDATES)

    if ref_col is not None:
        ref = gdf[ref_col].fillna("").astype(str).str.strip()
    else:
        ref = pd.Series("", index=gdf.index)

    if name_col is not None:
        name = gdf[name_col].fillna("").astype(str).str.strip()
    else:
        name = pd.Series("", index=gdf.index)

    ref = ref.where(ref != "", name)

    ref = ref.where(ref != "", "SIN_REF")

    return pd.Series(list(zip(ref, name, strict=False)), index=gdf.index, dtype="object")


def merge_fragments(geoms):
    """Junta una lista de LineStrings en la menor cantidad de piezas posible."""
    unioned = unary_union(list(geoms))
    # Si el grupo entero ya forma una sola linea continua, unary_union
    # devuelve directamente un LineString (no un MultiLineString), y
    # pasarle eso a linemerge() rompe. En ese caso ya esta mergeado.
    if isinstance(unioned, LineString):
        return [unioned]
    merged = linemerge(unioned)
    if isinstance(merged, LineString):
        return [merged]
    if isinstance(merged, MultiLineString):
        return list(merged.geoms)
    return []


def process(input_path, output_path, road_names=None) -> None:
    gdf = gpd.read_file(input_path)
    print(f"[load] {len(gdf)} features, CRS={gdf.crs}")

    gdf = gdf.explode(index_parts=False, ignore_index=True)
    gdf = gdf[gdf.geometry.type == "LineString"]

    gdf = filter_main_roads(gdf)
    if gdf.empty:
        print("No quedaron features tras el filtro. Revisa MAIN_ROAD_CLASSES / columna de clase.")
        return

    gdf = gdf.assign(_group_key=get_group_key(gdf))

    original_crs = gdf.crs
    metric_crs = gdf.estimate_utm_crs()
    gdf_m = gdf.to_crs(metric_crs)

    out_records = []
    for (ref, name), group in gdf_m.groupby("_group_key"):
        merged_lines = merge_fragments(group.geometry.tolist())

        out_records.extend(
            {"ref": ref, "name": name or None, "geometry": geom} for geom in merged_lines
        )

    out_gdf = gpd.GeoDataFrame(out_records, crs=metric_crs).to_crs(original_crs)

    if road_names:
        before = len(out_gdf)
        out_gdf = out_gdf[out_gdf["ref"].astype(str).str.startswith(tuple(road_names))]
        print(f"[road-name] filtro {road_names}* -> {len(out_gdf)}/{before} lineas")

    out_gdf.to_file(output_path, driver="GeoJSON")

    print(f"[done] {len(gdf)} fragmentos filtrados -> {len(out_gdf)} lineas finales")
    print(f"[done] escrito en {output_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", help="geojson de entrada (rutas sin filtrar)")
    ap.add_argument("--output", help="geojson de salida")
    ap.add_argument(
        "--road-names",
        nargs="+",
        default=None,
        help="filtra por rutas cuyo ref/name empiece con cualquiera de estos strings",
    )
    args = ap.parse_args()
    process(args.input, args.output, road_names=args.road_names)
