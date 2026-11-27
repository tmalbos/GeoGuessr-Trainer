"""geospatial_files.py — A geometry upload -> staged parquet, with its browser preview and region stats."""

import json
import uuid
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from fastapi import HTTPException

GEOM_KINDS = ("polygons", "lines", "points")
GEOM_KIND = {"Polygon": "polygons", "LineString": "lines", "Point": "points"}

PREVIEW_PRECISION = 1e-5  # ~1 m, only for the GeoJSON sent to the browser
PREVIEW_MAX_FEATURES = 5000
MAX_SHADE = 3  # 4 shades in total (0 = main tone); keep equal to SHADES - 1 in ShadeDraw.jsx


def _vertices(geoms) -> int:
    return int(shapely.get_num_coordinates(geoms).sum())


def _shades_of(gdf: gpd.GeoDataFrame) -> np.ndarray:
    """The `shade` column of a geometry file as ints 0..MAX_SHADE (zeros when it has none)."""
    if "shade" not in gdf.columns:
        return np.zeros(len(gdf), dtype=int)
    col = pd.to_numeric(gdf["shade"], errors="coerce").fillna(0)
    return col.astype(int).clip(0, MAX_SHADE).to_numpy()


def _to_geojson(
    geoms,
    limit: int | None = None,
    shades: np.ndarray | None = None,
    names: np.ndarray | None = None,
    colors: np.ndarray | None = None,
    extras: np.ndarray | None = None,
    blurs: np.ndarray | None = None,
    heights: np.ndarray | None = None,
    precision: float | None = PREVIEW_PRECISION,
) -> dict:
    """GeometryCollection for the browser. When `shades` (one per geometry) has anything above 0 it is sent
    along as `shades`, in the same order as `geometries`.

    `precision` rounds the coordinates (`None` keeps them exact, for the editor that builds on them).
    """
    if limit and len(geoms) > limit:
        step = -(-len(geoms) // limit)
        geoms = geoms[::step]
        if shades is not None:
            shades = shades[::step]
        if names is not None:
            names, colors = names[::step], colors[::step]
        if extras is not None:
            extras = extras[::step]
        if blurs is not None:
            blurs, heights = blurs[::step], heights[::step]
    g = shapely.set_precision(geoms, precision) if precision else geoms
    keep = ~shapely.is_empty(g)
    g = g[keep]
    out = {
        "type": "GeometryCollection",
        "geometries": [json.loads(s) for s in shapely.to_geojson(g)],
    }
    if shades is not None and shades[keep].any():
        out["shades"] = [int(v) for v in shades[keep]]
    if names is not None:
        out["names"] = [str(v) for v in names[keep]]
        out["colors"] = [str(v) for v in colors[keep]]
    if blurs is not None:
        out["extra_blurs"] = [bool(v) for v in blurs[keep]]
        out["extra_heights"] = [int(v) for v in heights[keep]]
    if extras is not None:
        out["extras"] = [str(v) for v in extras[keep]]
    return out


NAME_FIELDS = ("name", "nombre", "label", "title", "category", "tag", "id")


def _topo_features(topo: dict) -> list[dict]:
    """TopoJSON -> GeoJSON features (every object), decoding the shared arcs."""
    tr = topo.get("transform")

    def pos(p):
        if not tr:
            return [p[0], p[1]]
        return [
            p[0] * tr["scale"][0] + tr["translate"][0],
            p[1] * tr["scale"][1] + tr["translate"][1],
        ]

    arcs = []
    for arc in topo.get("arcs", []):
        pts, x, y = [], 0, 0
        for p in arc:
            if tr:
                x += p[0]
                y += p[1]
                pts.append(pos((x, y)))
            else:
                pts.append([p[0], p[1]])
        arcs.append(pts)

    def line(idx):
        out: list = []
        for i in idx:
            seg = arcs[i] if i >= 0 else arcs[~i][::-1]
            out.extend(seg if not out else seg[1:])
        return out

    def geom(o):
        t = o.get("type")
        if t == "Point":
            return {"type": t, "coordinates": pos(o["coordinates"])}
        if t == "MultiPoint":
            return {"type": t, "coordinates": [pos(p) for p in o["coordinates"]]}
        if t == "LineString":
            return {"type": t, "coordinates": line(o["arcs"])}
        if t == "MultiLineString":
            return {"type": t, "coordinates": [line(a) for a in o["arcs"]]}
        if t == "Polygon":
            return {"type": t, "coordinates": [line(r) for r in o["arcs"]]}
        if t == "MultiPolygon":
            return {"type": t, "coordinates": [[line(r) for r in p] for p in o["arcs"]]}
        return None

    feats = []
    for obj in topo.get("objects", {}).values():
        items = obj.get("geometries", []) if obj.get("type") == "GeometryCollection" else [obj]
        for it in items:
            g = geom(it)
            if g:
                feats.append(
                    {"type": "Feature", "properties": it.get("properties") or {}, "geometry": g}
                )
    return feats


def _auto_names(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Every distinct value of the name-like column becomes its own named, colored category."""
    import colorsys

    cols = {str(c).lower(): c for c in gdf.columns if c != gdf.geometry.name}
    field = next((cols[k] for k in NAME_FIELDS if k in cols), None)
    if field is None:
        field = next((c for c in cols.values() if gdf[c].dtype == object), None)
    if field is None:
        return gdf
    names = gdf[field].fillna("").astype(str).str.strip().replace("", "Unnamed")
    hexes = {}
    for i, n in enumerate(dict.fromkeys(names)):
        r, g, b = colorsys.hls_to_rgb(((i * 137.5) % 360) / 360, 0.55, 0.65)
        hexes[n] = f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"
    gdf = gdf.copy()
    gdf["name"] = names
    if "color" not in gdf.columns:
        gdf["color"] = names.map(hexes)
    return gdf


def _read_geo(data: bytes, suffix: str, tmp_dir: Path) -> gpd.GeoDataFrame:
    if suffix in {".json", ".topojson"}:
        try:
            doc = json.loads(data)
        except ValueError:
            doc = None
        if isinstance(doc, dict) and doc.get("type") == "Topology":
            feats = _topo_features(doc)
            if not feats:
                raise HTTPException(422, "No geometries found in this TopoJSON")
            return gpd.GeoDataFrame.from_features(feats, crs=4326)
    tmp = tmp_dir / f"{uuid.uuid4().hex}.upload{suffix}"
    tmp.write_bytes(data)
    try:
        if suffix == ".parquet":
            return gpd.read_parquet(tmp)
        if suffix == ".zip":
            return gpd.read_file(f"zip://{tmp}")
        if suffix == ".shp":
            msg = "A lone .shp is not enough. Zip the .shp, .shx, .dbf and .prj together."
            raise HTTPException(422, msg)
        return gpd.read_file(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def _process_geo(
    data: bytes, suffix: str, out: Path, outline=None, named: bool = False
) -> tuple[str, dict]:
    gdf = _read_geo(data, suffix, out.parent)
    gdf = gdf.set_crs(4326) if gdf.crs is None else gdf.to_crs(4326)
    if named and not {"name", "color"} <= set(gdf.columns):
        gdf = _auto_names(gdf)
    arr = gdf.geometry.to_numpy()
    present = ~shapely.is_missing(arr)
    arr = arr[present]
    shades = np.column_stack(
        [_shades_of(gdf)[present], np.flatnonzero(present)]
    )  # shade, source row
    parts, src = shapely.get_parts(arr, return_index=True)
    shades = shades[src]
    keep = ~shapely.is_empty(parts)
    parts, shades = parts[keep], shades[keep]

    kinds = {GEOM_KIND[g.geom_type] for g in parts if g.geom_type in GEOM_KIND}
    if not kinds:
        raise HTTPException(422, "No polygons, lines or points found in this file")
    if len(kinds) > 1:
        msg = f"This file mixes {' and '.join(sorted(kinds))}. Split it into one file per geometry type."
        raise HTTPException(422, msg)
    kind = kinds.pop()

    if kind == "polygons":
        parts, src = shapely.get_parts(shapely.make_valid(parts), return_index=True)
        shades = shades[src]
        keep = ~shapely.is_empty(parts)
        parts, shades = parts[keep], shades[keep]
    keep = np.array([GEOM_KIND.get(g.geom_type) == kind for g in parts], dtype=bool)
    parts, shades = parts[keep], shades[keep]

    before = _vertices(parts)
    keep = ~shapely.is_empty(parts)
    parts, shades = parts[keep], shades[keep]
    if outline is not None and not outline.is_empty:
        clipped, src = shapely.get_parts(
            shapely.intersection(parts, shapely.make_valid(outline)), return_index=True
        )
        shades = shades[src]
        keep = np.array([GEOM_KIND.get(g.geom_type) == kind for g in clipped], dtype=bool)
        parts, shades = clipped[keep], shades[keep]
        if not len(parts):
            raise HTTPException(422, "Nothing in this file falls inside the country.")
    rows, shades = shades[:, 1], shades[:, 0]

    out = out.with_name(f"{out.stem}.{kind}{out.suffix}")
    cols = {}
    if kind == "polygons" and shades.any():
        cols["shade"] = shades
    if kind == "polygons" and "drawn" in gdf.columns:
        cols["drawn"] = np.ones(len(parts), dtype=bool)  # made with the shade draw tool
    if kind == "polygons" and {"name", "color"} <= set(gdf.columns):
        cols["name"] = gdf["name"].astype(str).to_numpy()[rows]
        cols["color"] = gdf["color"].astype(str).to_numpy()[rows]
        if "extra" in gdf.columns:
            cols["extra"] = gdf["extra"].fillna("").astype(str).to_numpy()[rows]
        if {"extra_blur", "extra_max_height"} <= set(gdf.columns):
            cols["extra_blur"] = gdf["extra_blur"].fillna(False).astype(bool).to_numpy()[rows]
            cols["extra_max_height"] = (
                pd.to_numeric(gdf["extra_max_height"], errors="coerce")
                .fillna(0)
                .astype(int)
                .to_numpy()[rows]
            )
    gpd.GeoDataFrame(cols, geometry=parts, crs=4326).to_parquet(out, compression="zstd")
    return kind, {
        "kind": kind,
        "features": len(parts),
        "vertices_before": before,
        "vertices_after": _vertices(parts),
        "bytes_in": len(data),
        "bytes_out": out.stat().st_size,
        "bbox": [float(v) for v in shapely.total_bounds(parts)],
        "geojson": _to_geojson(
            parts,
            PREVIEW_MAX_FEATURES,
            shades,
            cols.get("name"),
            cols.get("color"),
            cols.get("extra"),
            cols.get("extra_blur"),
            cols.get("extra_max_height"),
        ),
        "stats": region_stats(parts, shades, kind),  # pre-fills the ratings of a new clue
    }


# ── region stats ───────────────────────────────────────────────────────────

MERGE_TOL_DEG = 0.0006  # ~65 m: gaps smaller than this between parts don't separate them
MAX_COMPONENTS = 500  # more than this and the spread is measured on an evenly spaced subset
SAMPLES = 64  # points taken along each component


def _km(pts: np.ndarray) -> np.ndarray:
    lat0 = np.radians(float(pts[:, 1].mean()))
    return np.column_stack([pts[:, 0] * 111.32 * np.cos(lat0), pts[:, 1] * 110.57])


def _mean_pairwise(xy: np.ndarray) -> float:
    n = len(xy)
    if n < 2:
        return 0.0
    d = np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1])
    return float(d.sum() / (n * (n - 1)))


def _sample(part) -> np.ndarray:
    kind = part.geom_type
    if kind == "Point":
        return np.array([[part.x, part.y]], float)
    line = part.exterior if kind == "Polygon" else part
    if not line.length:
        return shapely.get_coordinates(part)[:1]
    at = np.linspace(0, line.length, SAMPLES, endpoint=False)
    return shapely.get_coordinates(shapely.line_interpolate_point(line, at))


def region_stats(parts: np.ndarray, shades: np.ndarray, kind: str) -> dict | None:
    if not len(parts):
        return None
    merged = parts if kind == "points" else shapely.buffer(parts, MERGE_TOL_DEG)
    comps = [c for c in shapely.get_parts(shapely.union_all(merged)) if not c.is_empty]
    if not comps:
        return None
    step = max(1, len(comps) // MAX_COMPONENTS)
    spreads = [_mean_pairwise(_km(_sample(c))) for c in comps[::step]]
    return {
        "polygons": len(parts) if kind == "polygons" else 0,
        "shades": len(np.unique(shades)) if len(shades) else 0,
        "components": len(comps),
        "spread_km": round(float(np.mean(spreads)), 3),
    }
