"""osm_extract.py — Technical tool: open any .osm.pbf, pick what to extract, export it as TopoJSON.
Also splits a multi-country file into one .osm.pbf per country.

Flow:
    (the file is opened by /technical/pbf/open or /technical/pbf/upload; the token is shared)
    POST /technical/osm/countries  {token}                         -> countries whose boundary is complete in the file
    POST /technical/osm/split      {token, countries, folder, strategy} -> one .osm.pbf per country (needs osmium-tool)
    POST /technical/osm/fields     {token, layer, sample}          -> every field (column + tag key) with its common values
    POST /technical/osm/preview    {token, layer, filters, limit}  -> a few features to look at on the map
    POST /technical/osm/export     {token, layer, filters, fields, name} -> TopoJSON download

Reading goes through GDAL's OSM driver (pyogrio): layers points / lines / multilinestrings / multipolygons,
the same ones extract_roads.py uses. A "field" is a GDAL column or a key inside other_tags; a filter is
"key is one of these values" (no values = the key just has to exist). Filters are pushed down to GDAL.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import threading
from collections import Counter
from pathlib import Path
from typing import Literal

import numpy as np
import shapely
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from src.api.routers import pbf_admin as pbf

log = logging.getLogger("uvicorn.error")
router = APIRouter()

_read_ways = pbf._read_ways  # noqa: SLF001
_assemble = pbf._assemble  # noqa: SLF001
_clean = pbf._clean  # noqa: SLF001
_oneline = pbf._oneline  # noqa: SLF001

MAX_VALUES = 2000  # distinct values remembered per key while scanning
TOP_VALUES = 30
MAX_KEYS = 500
SKIP_VALUES = {
    "osm_id",
    "osm_way_id",
    "z_order",
}  # listed as fields, but their values are not useful as filters
GEOM_OK = [0, 1, 3, 4, 5, 6]  # point, line, polygon and their multi versions
SPLIT_SIMPLIFY = 0.0003  # degrees (~30 m) on the country polygon handed to osmium
PALETTE = ("#6cc0e5", "#ff7357", "#cdbf93", "#3fb97a", "#b07aa1", "#edc948", "#76b7b2", "#f28e2b")
LAYER_IDS = {"multipolygons", "lines", "multilinestrings", "points"}

_GDAL = threading.Lock()  # one pass over the file at a time
_COMPUTE = threading.Lock()  # country detection / splitting

_TAG = re.compile(r'"((?:[^"\\]|\\.)*)"=>"((?:[^"\\]|\\.)*)"')


# ── helpers ────────────────────────────────────────────────────────────────


def _unesc(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", s)


def _tags(raw) -> dict[str, str]:
    """GDAL's other_tags ("k"=>"v","k2"=>"v2") as a dict."""
    if not isinstance(raw, str) or not raw:
        return {}
    return {_unesc(k): _unesc(v) for k, v in _TAG.findall(raw)}


def _q(s: str) -> str:
    return s.replace("'", "''")


def _safe(s: str) -> str:
    return re.sub(r"[^\w.-]+", "_", s).strip("_") or "file"


def _stem(path: Path) -> str:
    name = path.name
    for suffix in (".osm.pbf", ".pbf"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return name


_dataset = pbf._dataset  # noqa: SLF001  (same open file as the admin tool)


def _check_layer(layer: str) -> None:
    if layer not in LAYER_IDS:
        raise HTTPException(422, "Unknown layer")


def _guard(what: str, fn, *args):
    try:
        return fn(*args)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("OSM extractor failed to %s", what)
        raise HTTPException(500, f"Could not {what}: {_oneline(e)}") from e


class Filter(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    values: list[str] = Field(default_factory=list, max_length=200)


def _columns(ds: dict, layer: str) -> list[str]:
    cols = ds.setdefault("cols", {}).get(layer)
    if cols is None:
        import pyogrio

        cols = [str(f) for f in pyogrio.read_info(str(ds["path"]), layer=layer)["fields"]]
        ds["cols"][layer] = cols
    return cols


def _where(filters: list[Filter], cols: list[str]) -> str | None:
    """OGR SQL WHERE for the filters: all filters must hold, any of a filter's values will do."""
    clauses = []
    for f in filters:
        key = f.key.strip()
        if not key:
            continue
        values = [v for v in f.values if v != ""]
        if key in cols:
            col = '"' + key.replace('"', "") + '"'
            if values:
                clauses.append(f"{col} IN ({', '.join(chr(39) + _q(v) + chr(39) for v in values)})")
            else:
                clauses.append(f"{col} IS NOT NULL")
        elif values:
            ors = [f'other_tags LIKE \'%"{_q(key)}"=>"{_q(v)}"%\'' for v in values]
            clauses.append("(" + " OR ".join(ors) + ")")
        else:
            clauses.append(f"other_tags LIKE '%\"{_q(key)}\"=>%'")
    return " AND ".join(clauses) or None


def _values(df, cols: list[str], keys: list[str]) -> dict[str, list[str]]:
    """Per key, the value of every row as a string ("" when missing)."""
    out: dict[str, list[str]] = {}
    tags = None
    for k in keys:
        if k in cols and k in df.columns:
            out[k] = [_clean(v) for v in df[k].tolist()]
        else:
            if tags is None:
                raw = df["other_tags"].tolist() if "other_tags" in df.columns else [""] * len(df)
                tags = [_tags(s) for s in raw]
            out[k] = [t.get(k, "") for t in tags]
    return out


def _table(ds: dict, layer: str, filters: list[Filter], keys: list[str], limit: int | None):
    """(geometries, {key: values}) of the features matching the filters, drawable geometries only."""
    import pyogrio

    cols = _columns(ds, layer)
    df = pyogrio.read_dataframe(
        str(ds["path"]), layer=layer, where=_where(filters, cols), max_features=limit
    )
    geoms = df.geometry.to_numpy()
    ok = np.isin(shapely.get_type_id(geoms), GEOM_OK) & ~shapely.is_empty(geoms)
    idx = np.flatnonzero(ok)
    vals = _values(df, cols, keys)
    return geoms[idx], {k: [v[i] for i in idx] for k, v in vals.items()}


def _feature(props: dict, geom_text: str) -> str:
    return (
        '{"type":"Feature","properties":'
        + json.dumps(props, ensure_ascii=False, separators=(",", ":"))
        + ',"geometry":'
        + geom_text
        + "}"
    )


class TokenBody(BaseModel):
    token: str


# ── fields ─────────────────────────────────────────────────────────────────


def _scan_fields(ds: dict, layer: str, sample: int) -> dict:
    import pyogrio

    with _GDAL:
        df = pyogrio.read_dataframe(
            str(ds["path"]), layer=layer, max_features=sample, read_geometry=False
        )
    cols = _columns(ds, layer)
    stats: dict[str, dict] = {}

    def entry(key: str, column: bool) -> dict:
        s = stats.get(key)
        if s is None:
            s = stats[key] = {"count": 0, "column": column, "values": Counter()}
        return s

    for c in cols:
        if c == "other_tags":
            continue
        s = entry(c, True)
        for v in df[c].tolist() if c in df.columns else []:
            t = _clean(v)
            if not t:
                continue
            s["count"] += 1
            if c not in SKIP_VALUES and (t in s["values"] or len(s["values"]) < MAX_VALUES):
                s["values"][t] += 1
    if "other_tags" in df.columns:
        for raw in df["other_tags"].tolist():
            for k, v in _tags(raw).items():
                s = entry(k, False)
                s["count"] += 1
                if v in s["values"] or len(s["values"]) < MAX_VALUES:
                    s["values"][v] += 1

    fields = [
        {
            "key": k,
            "count": s["count"],
            "column": s["column"],
            "values": [[v, n] for v, n in s["values"].most_common(TOP_VALUES)],
        }
        for k, s in stats.items()
    ]
    fields.sort(key=lambda f: (-f["count"], f["key"]))
    return {"fields": fields[:MAX_KEYS], "sampled": len(df), "partial": len(df) >= sample}


class FieldsBody(TokenBody):
    layer: str
    sample: int = Field(50_000, ge=1000, le=1_000_000)


@router.post("/technical/osm/fields")
async def fields_osm(body: FieldsBody):
    ds = _dataset(body.token)
    _check_layer(body.layer)
    return await run_in_threadpool(
        _guard, "scan this layer", _scan_fields, ds, body.layer, body.sample
    )


# ── preview / export ───────────────────────────────────────────────────────


class ViewBody(TokenBody):
    layer: str
    filters: list[Filter] = Field(default_factory=list, max_length=20)
    fields: list[str] = Field(default_factory=list, max_length=300)
    limit: int = Field(2000, ge=1, le=20000)
    name: str = Field("osm", max_length=60)


def _preview(ds: dict, body: ViewBody, gen: int) -> str:
    keys = list(dict.fromkeys(["name", *[f.key for f in body.filters]]))
    with _GDAL:
        if gen != ds.get("gen"):  # a newer request replaced this one: skip the pass over the file
            return '{"stale":true}'
        geoms, vals = _table(ds, body.layer, body.filters, keys, body.limit)
    texts = shapely.to_geojson(geoms) if len(geoms) else []
    first = body.filters[0].key if body.filters else None
    seen: dict[str, int] = {}
    feats = []
    for i, g in enumerate(texts):
        key_val = vals[first][i] if first else ""
        color = PALETTE[seen.setdefault(key_val, len(seen)) % len(PALETTE)]
        tag = vals["name"][i] or ", ".join(vals[f.key][i] for f in body.filters if vals[f.key][i])
        feats.append(_feature({"tag": tag or body.layer, "color": color, "shade": 0}, g))
    return '{"count":%d,"truncated":%s,"geojson":{"type":"FeatureCollection","features":[%s]}}' % (
        len(feats),
        "true" if len(feats) >= body.limit else "false",
        ",".join(feats),
    )


def _export(ds: dict, body: ViewBody) -> str:
    try:
        from topojson import Topology
    except ImportError as e:  # pragma: no cover
        raise HTTPException(503, "The topojson package is not installed on the server.") from e
    fields = list(dict.fromkeys(body.fields))
    with _GDAL:
        geoms, vals = _table(ds, body.layer, body.filters, fields, None)
    texts = shapely.to_geojson(geoms) if len(geoms) else []
    feats = [
        _feature({k: vals[k][i] for k in fields if vals[k][i]}, g) for i, g in enumerate(texts)
    ]
    if not feats:
        raise HTTPException(422, "Nothing matches this selection.")
    fc = json.loads('{"type":"FeatureCollection","features":[' + ",".join(feats) + "]}")
    return Topology(fc, object_name=_safe(body.name)).to_json()


@router.post("/technical/osm/preview")
async def preview_osm(body: ViewBody):
    ds = _dataset(body.token)
    _check_layer(body.layer)
    ds["gen"] = ds.get("gen", 0) + 1
    text = await run_in_threadpool(_guard, "read this layer", _preview, ds, body, ds["gen"])
    return Response(content=text, media_type="application/json")


@router.post("/technical/osm/export")
async def export_osm(body: ViewBody):
    ds = _dataset(body.token)
    _check_layer(body.layer)
    text = await run_in_threadpool(_guard, "export this layer", _export, ds, body)
    filename = f"{_safe(_stem(ds['path']))}-{_safe(body.name)}.topojson"
    return Response(
        content=text,
        media_type="application/topojson+json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── countries and splitting ────────────────────────────────────────────────


def _load_countries(ds: dict) -> None:
    """Countries whose whole boundary is in the file (a country file also holds incomplete relations of its
    neighbours; those are not countries you could split out). One pass over the boundary ways, cached.
    """
    with _COMPUTE:
        if ds.get("split_countries") is not None:
            return
        rels = {k: v for k, v in ds["rels"].items() if v["level"] == 2}
        wanted: set[int] = set()
        for c in ds["countries"]:
            r = rels.get(int(c["id"]))
            if r:
                wanted.update(r["outer"])
                wanted.update(r["inner"])
        ways = _read_ways(ds["path"], wanted, None) if wanted else {}
        polys = {}
        for c in ds["countries"]:
            r = rels.get(int(c["id"]))
            try:
                g = _assemble(r, ways) if r else None
            except Exception:
                log.warning("Could not assemble the boundary of %s", c["name"], exc_info=True)
                g = None
            if g is not None and not g.is_empty:
                polys[c["id"]] = g
        ds["split_polys"] = polys
        ds["split_countries"] = [c for c in ds["countries"] if c["id"] in polys]


@router.post("/technical/osm/countries")
async def countries_osm(body: TokenBody):
    ds = _dataset(body.token)
    await run_in_threadpool(_guard, "check the countries", _load_countries, ds)
    return {"countries": ds["split_countries"]}


class SplitBody(TokenBody):
    countries: list[str] = Field(min_length=1)
    folder: str = ""
    strategy: Literal["smart", "complete_ways", "simple"] = "smart"


def _split(ds: dict, body: SplitBody) -> dict:
    exe = shutil.which("osmium")
    if exe is None:
        raise HTTPException(
            503, "osmium-tool isn't installed or not on PATH. Install it, then restart."
        )
    _load_countries(ds)
    chosen = [c for c in ds["split_countries"] if c["id"] in set(body.countries)]
    if not chosen:
        raise HTTPException(422, "Pick at least one country.")
    folder = body.folder.strip().strip('"')
    if ds["temporary"] and not folder:
        raise HTTPException(422, "Enter a folder to save the country files in.")
    out_dir = Path(folder) if folder else ds["path"].parent / f"{_stem(ds['path'])}-countries"
    out_dir.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="osm-split-"))
    try:
        extracts, labels, used = [], {}, set()
        for c in chosen:
            label = _safe(c["code"] or c["name"])
            if label in used:
                label = f"{label}_{c['id']}"
            used.add(label)
            geom = shapely.simplify(ds["split_polys"][c["id"]], SPLIT_SIMPLIFY)
            poly = tmp / f"{label}.geojson"
            poly.write_text(
                json.dumps(
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": json.loads(shapely.to_geojson(geom)),
                    }
                ),
                encoding="utf-8",
            )
            out_name = f"{label}.osm.pbf"
            labels[out_name] = c["name"]
            extracts.append(
                {"output": out_name, "polygon": {"file_name": str(poly), "file_type": "geojson"}}
            )
        cfg = tmp / "config.json"
        cfg.write_text(
            json.dumps({"directory": str(out_dir), "extracts": extracts}), encoding="utf-8"
        )
        proc = subprocess.run(
            [
                exe,
                "extract",
                "--config",
                str(cfg),
                "--strategy",
                body.strategy,
                "--overwrite",
                str(ds["path"]),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode != 0:
            lines = (proc.stderr or proc.stdout or "").strip().splitlines()
            raise HTTPException(
                500, f"osmium failed: {(lines[-1] if lines else 'unknown error')[:120]}"
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    files = []
    for name, country in labels.items():
        p = out_dir / name
        if p.is_file():
            files.append(
                {"name": name, "path": str(p), "size": p.stat().st_size, "country": country}
            )
    return {"folder": str(out_dir), "files": files}


@router.post("/technical/osm/split")
async def split_osm(body: SplitBody):
    ds = _dataset(body.token)
    return await run_in_threadpool(_guard, "split this file", _split, ds, body)
