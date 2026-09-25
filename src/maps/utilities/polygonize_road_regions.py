#!/usr/bin/env python3
"""Split a country into non-overlapping regions by road-numbering class
(e.g. the 8xx roads, the 7xx roads, ...).

Approach: every raster cell of the country gets exactly one class, so overlaps
and gaps cannot exist. Per-class road density is blurred first (the
"averagization"), then each cell takes the class with the highest score.

Requires: geopandas, rasterio, scipy, topojson, shapely>=2.1

Example:
    python road_regions.py countries.topojson roads.geojson out.topojson \
        --name Ireland --name-field name --ref-field ref --digits 1

"""

import argparse
import math
import pathlib
import re

import geopandas as gpd
import numpy as np
import rasterio.features
import shapely
import topojson as tp
from rasterio.transform import from_origin
from scipy import ndimage as ndi
from shapely.geometry import shape
from shapely.ops import polygonize, unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
def load_country(path, layer, name_field, name):
    gdf = gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(4326)
    gdf = gdf.to_crs(4326)
    if name:
        hit = gdf[gdf[name_field].astype(str).str.lower() == name.lower()]
        if hit.empty:
            msg = (
                f"No feature with {name_field}={name!r}. "
                f"Examples: {gdf[name_field].astype(str).head(10).tolist()}"
            )
            raise SystemExit(msg)
        gdf = hit
    return unary_union(gdf.geometry.values)


def road_classes(ref, digits):
    """'R812' -> ['8'];  'R812;N4' -> ['8', '4'];  no number -> []."""
    if not isinstance(ref, str):
        return []
    out = set()
    for part in ref.split(";"):
        m = re.search(r"\d+", part)
        if m:
            out.add(m.group()[:digits])
    return sorted(out)


def load_roads(path, ref_field, digits):
    roads = gpd.read_file(path)
    if roads.crs is None:
        roads = roads.set_crs(4326)
    roads = roads.to_crs(4326)
    roads["cls"] = roads[ref_field].map(lambda r: road_classes(r, digits))
    roads = roads.explode("cls").dropna(subset=["cls"])
    roads = roads[~roads.geometry.is_empty]
    return roads[["cls", "geometry"]]


# --------------------------------------------------------------------------- #
# Raster labeling
# --------------------------------------------------------------------------- #
def fill_unassigned(labels, mask):
    """Give every unlabeled in-mask cell the label of the nearest labeled cell."""
    unassigned = labels < 0
    if not (unassigned & mask).any():
        return labels
    idx = ndi.distance_transform_edt(unassigned, return_distances=False, return_indices=True)
    return np.where(mask, labels[tuple(idx)], -1)


def remove_small_regions(labels, min_cells):
    """Un-label connected blobs smaller than min_cells (they get re-filled later)."""
    out = labels.copy()
    for c in np.unique(labels[labels >= 0]):
        comp, _ = ndi.label(labels == c)
        sizes = np.bincount(comp.ravel())
        small = np.where(sizes < min_cells)[0]
        small = small[small != 0]
        out[np.isin(comp, small)] = -1
    return out


def label_raster(country, roads, sigma_m, cell_m, min_area_frac):
    minx, miny, maxx, maxy = country.bounds
    pad = 2 * sigma_m
    minx, miny, maxx, maxy = minx - pad, miny - pad, maxx + pad, maxy + pad
    w = math.ceil((maxx - minx) / cell_m)
    h = math.ceil((maxy - miny) / cell_m)
    transform = from_origin(minx, maxy, cell_m, cell_m)
    kw = {"out_shape": (h, w), "transform": transform, "fill": 0, "dtype": "uint8"}

    # all_touched=True so the raster fully covers the country, edge to edge
    mask = rasterio.features.rasterize([(country, 1)], all_touched=True, **kw).astype(bool)

    classes = sorted(roads["cls"].unique())
    road_cells, scores = [], np.zeros((len(classes), h, w), np.float32)
    for i, c in enumerate(classes):
        geoms = roads.geometry[roads["cls"] == c]
        r = rasterio.features.rasterize([(g, 1) for g in geoms], **kw)
        road_cells.append(r.astype(bool))
        scores[i] = ndi.gaussian_filter(r.astype(np.float32), sigma_m / cell_m, truncate=4)

    labels = np.where(mask & (scores.max(0) > 0), scores.argmax(0), -1).astype(np.int32)
    labels = fill_unassigned(labels, mask)
    labels = remove_small_regions(labels, int(min_area_frac * mask.sum()))
    labels = fill_unassigned(labels, mask)

    # purity: share of each class's road cells that land inside its own region
    for i, c in enumerate(classes):
        total = road_cells[i].sum()
        inside = (road_cells[i] & (labels == i)).sum()
        print(f"  class {c}: {inside / max(total, 1):.0%} of road cells inside own region")

    return labels, transform, classes


# --------------------------------------------------------------------------- #
# Vector post-processing
# --------------------------------------------------------------------------- #
def vectorize(labels, transform):
    parts = {}
    for geom, val in rasterio.features.shapes(
        labels, mask=labels >= 0, transform=transform, connectivity=4
    ):
        parts.setdefault(int(val), []).append(shape(geom))
    return {k: unary_union(v) for k, v in parts.items()}


def clip_to_country(regions, country):
    """Clip regions to the true outline in a way that keeps shared borders identical:
    node all boundaries together, polygonize into faces, and assign each face
    inside the country to the region it belongs to.
    """
    lines = [country.boundary] + [g.boundary for g in regions.values()]
    faces = polygonize(unary_union(lines))
    keys = list(regions)
    tree = STRtree([regions[k] for k in keys])
    inside = prep(country)
    out = {k: [] for k in keys}
    for f in faces:
        p = f.representative_point()
        if not inside.contains(p):
            continue
        hit = tree.query(p, predicate="intersects")
        if len(hit):
            out[keys[hit[0]]].append(f)
    return {k: unary_union(v) for k, v in out.items() if v}


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("countries", help="national outlines (TopoJSON)")
    ap.add_argument("roads", help="roads (GeoJSON)")
    ap.add_argument("output", help="output TopoJSON")
    ap.add_argument("--layer", help="TopoJSON object name (if the file has several)")
    ap.add_argument("--name-field", default="name", help="property holding the country name")
    ap.add_argument("--name", help="country to process (omit if the file holds only one)")
    ap.add_argument("--ref-field", default="ref", help="road property holding the ref, e.g. R812")
    ap.add_argument(
        "--digits", type=int, default=1, help="leading digits of the number that define the class"
    )
    ap.add_argument(
        "--sigma-percentage",
        type=float,
        default=2,
        help="blur radius; bigger = smoother blobs (default 2%% of country size)",
    )
    ap.add_argument("--cell-km", type=float, help="raster cell size (default country size / 500)")
    ap.add_argument(
        "--min-area-frac",
        type=float,
        default=0.002,
        help="drop blobs smaller than this fraction of the country",
    )
    ap.add_argument(
        "--simplify-km", type=float, help="simplification of internal borders (default 2 cells)"
    )
    args = ap.parse_args()

    country_ll = load_country(args.countries, args.layer, args.name_field, args.name)
    roads_ll = load_roads(args.roads, args.ref_field, args.digits)
    print(f"{len(roads_ll)} road segments, classes: {sorted(roads_ll['cls'].unique())}")

    # Local equal-area projection so distances are in metres
    minx, miny, maxx, maxy = country_ll.bounds
    crs = f"+proj=laea +lat_0={(miny + maxy) / 2} +lon_0={(minx + maxx) / 2} +datum=WGS84 +units=m"
    country = gpd.GeoSeries([country_ll], crs=4326).to_crs(crs).iloc[0]
    roads = roads_ll.to_crs(crs)

    bx0, by0, bx1, by1 = country.bounds
    extent = max(bx1 - bx0, by1 - by0)
    cell_m = args.cell_km * 1000 if args.cell_km else extent / 500
    sigma_m = args.sigma_percentage / 100 * extent
    simplify_m = args.simplify_km * 1000 if args.simplify_km else 2 * cell_m
    print(
        f"cell {cell_m / 1000:.2f} km, sigma {sigma_m / 1000:.1f} km, simplify {simplify_m / 1000:.2f} km"
    )

    labels, transform, classes = label_raster(country, roads, sigma_m, cell_m, args.min_area_frac)

    regions = clip_to_country(vectorize(labels, transform), country)
    keys = sorted(regions)
    geoms = np.array([regions[k] for k in keys], dtype=object)

    if simplify_m > 0:
        # topology-preserving: shared borders stay shared, outer border untouched
        geoms = shapely.coverage_simplify(geoms, simplify_m, simplify_boundary=False)
    print("coverage valid:", bool(shapely.coverage_is_valid(geoms).all()))

    out = gpd.GeoDataFrame(
        {"NAME_1": [classes[k] for k in keys]}, geometry=list(geoms), crs=crs
    ).to_crs(4326)
    topo = tp.Topology(out, object_name="regions", prequantize=1e5)
    json_text = topo.to_json()
    json_text = re.sub(r"(-?\d+\.\d{5})\d+", r"\1", json_text)

    pathlib.Path(args.output).write_text(json_text, encoding="utf-8")
    print(f"wrote {args.output} ({len(out)} regions)")


if __name__ == "__main__":
    main()
