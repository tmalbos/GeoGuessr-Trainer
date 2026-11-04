"""osm_extract.py — Technical tool: open any .osm.pbf, pick what to extract, export it as TopoJSON.
Also splits a multi-country file into one .osm.pbf per country.

Flow:
    (the file is opened by /technical/pbf/open or /technical/pbf/upload; the token is shared)
    GET  /technical/osm/osmium     -> is osmium-tool found?        PUT = save its path
    POST /technical/osm/countries  {token}  -> starts a job: countries whose boundary is complete in the file
    POST /technical/osm/split      {token, countries, folder} -> starts a job: one .osm.pbf per country
    GET  /technical/osm/job?token= -> state of the running / last job (the UI polls it)
    POST /technical/osm/fields     {token, layer, sample}          -> every field (column + tag key) with its common values
    POST /technical/osm/preview    {token, layer, filters, limit}  -> a few features to look at on the map
    POST /technical/osm/export     {token, layer, filters, fields, name} -> TopoJSON download

Splitting uses osmium-tool (C++), see src/geo/osmium_tool.py:
  - countries: `osmium tags-filter` pulls only the admin_level=2 relations and the relations with an ISO3166-1 code
    (+ their ways and nodes) into a small cached file; the country shapes are built from that instead of re-reading
    the whole big file. Territories (relations below level 2 whose ISO code is a bare two-letter code, e.g. CC, CX,
    NF) count as countries of their own, and their area is cut out of every country that overlaps it.
  - split: the chosen countries are grouped into BALANCED BATCHES (see _plan_batches) and each batch is one
    `osmium extract --strategy simple` run + cleanup. Memory use of osmium grows with the number and size of the
    countries cut in the same run, so one giant run can run out of memory late in the job. Batches keep every run
    well under that limit. If a batch still fails (out of memory...), it is split in two and retried, so a job never
    dies because one batch was too big. Each batch is cleaned right after it is extracted: ways that
    lost nodes are removed, and relation members pointing to things that no longer exist are dropped
    (relations left with nothing are removed).
    The cleanup finds the damage with `osmium check-refs` (C++, no Python loop over nodes), only reads the
    relations in Python, and cleans several countries at the same time.

Reading for preview / export goes through GDAL's OSM driver (pyogrio): layers points / lines / multilinestrings /
multipolygons. A "field" is a GDAL column or a key inside other_tags; a filter is "key is one of these values"
(no values = the key just has to exist). Filters are pushed down to GDAL.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import shapely
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from src.api.routers import pbf_admin as pbf
from src.geo import osmium_tool

log = logging.getLogger("uvicorn.error")
router = APIRouter()

_read_ways = pbf._read_ways  # noqa: SLF001
_assemble = pbf._assemble  # noqa: SLF001
_clean = pbf._clean  # noqa: SLF001
_oneline = pbf._oneline  # noqa: SLF001
_polys = pbf._polys  # noqa: SLF001
CACHE_DIR = pbf.CACHE_DIR

MAX_VALUES = 2000  # distinct values remembered per key while scanning
TOP_VALUES = 30
MAX_KEYS = 500
SKIP_VALUES = {
    "osm_id",
    "osm_way_id",
    "z_order",
}  # listed as fields, but their values are not useful as filters
GEOM_OK = [0, 1, 3, 4, 5, 6]  # point, line, polygon and their multi versions
PALETTE = ("#6cc0e5", "#ff7357", "#cdbf93", "#3fb97a", "#b07aa1", "#edc948", "#76b7b2", "#f28e2b")
LAYER_IDS = {"multipolygons", "lines", "multilinestrings", "points"}
TERRITORY_MIN_OVERLAP = (
    0.05  # a territory is cut out of a country when at least this share of it lies inside
)
CLEAN_WORKERS = (
    4  # countries cleaned at the same time (most of the time is spent in osmium, not Python)
)

# ── split batching (tune here) ─────────────────────────────────────────────
# Estimated amount of the source file (GB) one osmium run may cover. South America (3.5 GB, all in one run) worked
# but slowly; Africa (7 GB) failed in one run and in two halves, and four quarters took ~11 min each.
# Lower = more, smaller batches (safer); higher = fewer, bigger batches.
BATCH_GB = 2.0
MAX_BATCH_COUNTRIES = 14  # never more countries than this in one run, whatever their size
# A country's cost is its land area ** WEIGHT_EXP: dense / large countries weigh more than small ones, but a big
# desert does not weigh as much as its area suggests (1.0 = pure area, 0 = every country weighs the same).
WEIGHT_EXP = 0.6

_GDAL = threading.Lock()  # one pass over the file at a time
_COMPUTE = threading.Lock()  # country detection
_JOB_LOCK = threading.Lock()

_TAG = re.compile(r'"((?:[^"\\]|\\.)*)"=>"((?:[^"\\]|\\.)*)"')
_REF = re.compile(r"\b([nwr])(\d+) in ([nwr])(\d+)\b")  # a line of `osmium check-refs --show-ids`


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


# ── osmium-tool ────────────────────────────────────────────────────────────


class OsmiumBody(BaseModel):
    path: str = Field("", max_length=500)


@router.get("/technical/osm/osmium")
async def osmium_status():
    return await run_in_threadpool(osmium_tool.status)


@router.put("/technical/osm/osmium")
async def osmium_save(body: OsmiumBody):
    try:
        return await run_in_threadpool(osmium_tool.save, body.path)
    except osmium_tool.OsmiumMissingError as e:
        raise HTTPException(422, str(e)) from e


def _need_osmium() -> None:
    try:
        osmium_tool.require()
    except osmium_tool.OsmiumMissingError as e:
        raise HTTPException(422, str(e)) from e


# ── background jobs (countries / split can take many minutes) ──────────────


def _job_view(ds: dict) -> dict:
    j = ds.get("job")
    if j is None:
        return {"state": "idle"}
    end = j.get("ended") or time.time()
    return {
        "kind": j["kind"],
        "state": j["state"],
        "stage": j["stage"],
        "elapsed": round(end - j["started"]),
        "result": j.get("result"),
        "error": j.get("error"),
        "detail": j.get("detail", ""),
    }


def _start_job(ds: dict, kind: str, fn) -> dict:
    with _JOB_LOCK:
        cur = ds.get("job")
        if cur is not None and cur["state"] == "running":
            raise HTTPException(409, "Another job on this file is still running.")
        job = {"kind": kind, "state": "running", "stage": "Starting", "started": time.time()}
        ds["job"] = job

    def progress(stage: str) -> None:
        job["stage"] = stage

    def work() -> None:
        try:
            job["result"] = fn(progress)
            job["state"] = "done"
        except HTTPException as e:
            job["error"], job["state"] = str(e.detail), "error"
        except osmium_tool.OsmiumError as e:
            job["error"], job["detail"], job["state"] = e.message, e.detail, "error"
        except osmium_tool.OsmiumMissingError as e:
            job["error"], job["state"] = str(e), "error"
        except Exception as e:
            log.exception("OSM %s job failed", kind)
            job["error"], job["state"] = _oneline(e), "error"
        finally:
            job["ended"] = time.time()

    threading.Thread(target=work, daemon=True, name=f"osm-{kind}").start()
    return _job_view(ds)


@router.get("/technical/osm/job")
async def job_osm(token: str):
    return _job_view(_dataset(token))


# ── countries and splitting ────────────────────────────────────────────────


def _boundaries_file(ds: dict, progress) -> Path:
    """Small .osm.pbf with only the admin_level=2 relations, the relations carrying an ISO3166-1 code (territories)
    and the ways / nodes they use. Cached on disk, keyed by the big file's path, size and modification time, so
    reopening the same file is instant.
    """
    src: Path = ds["path"]
    st = src.stat()
    key = hashlib.sha1(f"{src.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16]
    out = CACHE_DIR / f"admin2t-{key}.osm.pbf"
    if out.is_file() and out.stat().st_size > 0:
        return out
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    part = CACHE_DIR / f"admin2t-{key}.part.osm.pbf"
    progress("Pulling the country borders out of the file")
    osmium_tool.run(
        [
            "tags-filter",
            str(src),
            "r/admin_level=2",
            "r/ISO3166-1",
            "r/ISO3166-1:alpha2",
            "--output",
            str(part),
            "--overwrite",
        ]
    )
    part.replace(out)
    return out


def _territories(ds: dict) -> list[dict]:
    """Territories that count as countries of their own: relations below level 2 whose ISO code is a bare
    two-letter code (CC, CX, NF...), not a region code like AU-NSW. One per code.
    """
    taken = {c["code"] for c in ds["countries"] if c["code"]}
    out = []
    for rid, r in ds["rels"].items():
        iso = str(r["iso"]).strip().upper()
        if r["level"] > 2 and len(iso) == 2 and iso.isalpha() and iso not in taken:
            taken.add(iso)
            out.append({"id": str(rid), "name": r["name"], "code": iso})
    return out


def _minus(g, other):
    try:
        return shapely.difference(g, other)
    except Exception:  # noqa: BLE001
        return shapely.difference(shapely.make_valid(g), shapely.make_valid(other))


def _load_countries(ds: dict, progress) -> None:
    """Countries and territories whose whole boundary is in the file (a country file also holds incomplete relations
    of its neighbours; those are not countries you could split out). Built from the small filtered file, cached.
    Every territory's area is cut out of the countries that overlap it.
    """
    with _COMPUTE:
        if ds.get("split_countries") is not None:
            return
        small = _boundaries_file(ds, progress)
        progress("Building the country shapes")
        rels = ds["rels"]
        territories = _territories(ds)
        cands = ds["countries"] + territories
        wanted: set[int] = set()
        for c in cands:
            r = rels.get(int(c["id"]))
            if r:
                wanted.update(r["outer"])
                wanted.update(r["inner"])
        ways = _read_ways(small, wanted) if wanted else {}
        polys = {}
        for c in cands:
            r = rels.get(int(c["id"]))
            try:
                g = _assemble(r, ways) if r else None
            except Exception:
                log.warning("Could not assemble the boundary of %s", c["name"], exc_info=True)
                g = None
            if g is not None and not g.is_empty:
                polys[c["id"]] = g
        terr_ids = {t["id"] for t in territories}
        for tid in terr_ids & polys.keys():
            t = shapely.make_valid(polys[tid])
            if t.is_empty or t.area <= 0:
                continue
            for cid in list(polys):
                if cid in terr_ids:
                    continue
                g = polys[cid]
                if not g.intersects(t):
                    continue
                if (
                    shapely.intersection(shapely.make_valid(g), t).area
                    < TERRITORY_MIN_OVERLAP * t.area
                ):
                    continue
                rest = _polys(_minus(g, t))
                if rest is not None:
                    polys[cid] = rest
        ds["split_polys"] = polys
        ds["split_countries"] = sorted(
            (c for c in cands if c["id"] in polys), key=lambda c: c["name"].casefold()
        )


def _countries_job(ds: dict, progress) -> dict:
    _load_countries(ds, progress)
    return {"countries": ds["split_countries"]}


@router.post("/technical/osm/countries")
async def countries_osm(body: TokenBody):
    ds = _dataset(body.token)
    _need_osmium()
    return _start_job(ds, "countries", lambda progress: _countries_job(ds, progress))


class SplitBody(TokenBody):
    countries: list[str] = Field(min_length=1)
    folder: str = ""


# ── batching ───────────────────────────────────────────────────────────────


def _weights(ds: dict) -> dict[str, float]:
    """Cost estimate of every splittable country (cached on the dataset): land area (km2) ** WEIGHT_EXP.
    Only relative values matter. Computed over ALL countries of the file, so a country's share of the file does not
    change with what the user ticked.
    """
    w = ds.get("split_weights")
    if w is None:
        w = {}
        for cid, g in ds["split_polys"].items():
            lat = g.centroid.y if not g.is_empty else 0.0
            km2 = g.area * 111.32**2 * max(0.05, math.cos(math.radians(lat)))
            w[cid] = max(km2, 1.0) ** WEIGHT_EXP
        ds["split_weights"] = w
    return w


def _balance(items: list[dict], weights: dict[str, float], k: int) -> list[list[dict]]:
    """Spread `items` over `k` groups so the total weight of each group is as equal as possible (heaviest first,
    each one into the lightest group so far). Empty groups are dropped.
    """
    k = max(1, min(k, len(items)))
    groups: list[list[dict]] = [[] for _ in range(k)]
    load = [0.0] * k
    for c in sorted(items, key=lambda c: -weights[c["id"]]):
        i = min(range(k), key=lambda j: (load[j], len(groups[j])))
        groups[i].append(c)
        load[i] += weights[c["id"]]
    return [g for g in groups if g]


def _plan_batches(ds: dict, chosen: list[dict], weights: dict[str, float]) -> list[list[dict]]:
    """Balanced batches for the chosen countries.

    Estimated work = size of the file * (weight of the chosen countries / weight of all countries in the file).
    The number of batches is that work divided by BATCH_GB (and at least enough for MAX_BATCH_COUNTRIES per batch);
    then the countries are spread over them so every batch costs about the same.
    """
    total = sum(weights.values()) or 1.0
    share = sum(weights[c["id"]] for c in chosen) / total
    need_gb = (ds["size"] / 1e9) * share
    k = max(math.ceil(need_gb / BATCH_GB), math.ceil(len(chosen) / MAX_BATCH_COUNTRIES), 1)
    return _balance(chosen, weights, k)


def _run_batch(
    ds: dict, batch: list[dict], labels: dict[str, str], out_dir: Path, progress
) -> None:
    """One osmium extract run for `batch`, then the cleanup of its files. The work folder is always removed."""
    work = Path(tempfile.mkdtemp(prefix="split-", dir=CACHE_DIR))
    try:
        extracts = []
        for c in batch:
            label = labels[c["id"]]
            shape = work / f"{label}.geojson"
            shape.write_text(
                json.dumps(
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": json.loads(shapely.to_geojson(ds["split_polys"][c["id"]])),
                    }
                ),
                encoding="utf-8",
            )
            extracts.append(
                {
                    "output": f"{label}.osm.pbf",
                    "output_format": "pbf",
                    "polygon": {"file_name": str(shape), "file_type": "geojson"},
                }
            )
        config = work / "extract.json"
        config.write_text(
            json.dumps({"directory": str(out_dir), "extracts": extracts}), encoding="utf-8"
        )
        osmium_tool.run(
            [
                "extract",
                "--config",
                str(config),
                "--strategy",
                "simple",
                "--overwrite",
                str(ds["path"]),
            ]
        )
        todo = {labels[c["id"]]: out_dir / f"{labels[c['id']]}.osm.pbf" for c in batch}
        todo = {label: p for label, p in todo.items() if p.is_file()}
        if todo:
            names = {labels[c["id"]]: c["name"] for c in batch}
            workers = max(1, min(CLEAN_WORKERS, os.cpu_count() or 2, len(todo)))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="osm-clean") as pool:
                futures = {
                    pool.submit(_clean_file, p, work, label): label for label, p in todo.items()
                }
                for n, fut in enumerate(as_completed(futures), 1):
                    label = futures[fut]
                    log.info("Cleaned %s: %s", todo[label].name, fut.result())
                    progress(f"Cleaned {names[label]} ({n} of {len(todo)} in this batch)")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _split(ds: dict, body: SplitBody, progress) -> dict:
    _load_countries(ds, progress)
    ids = set(body.countries)
    chosen = [c for c in ds["split_countries"] if c["id"] in ids]
    if not chosen:
        raise HTTPException(422, "Pick at least one country.")
    folder = body.folder.strip().strip('"')
    if ds["temporary"] and not folder:
        raise HTTPException(422, "Enter a folder to save the country files in.")
    out_dir = Path(folder) if folder else ds["path"].parent / f"{_stem(ds['path'])}-countries"
    out_dir.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # File names are fixed up front so a country keeps its name whatever batch (or retry) it ends up in.
    labels: dict[str, str] = {}
    used: set[str] = set()
    for c in chosen:
        label = _safe(c["code"] or c["name"])
        if label in used:
            label = f"{label}_{c['id']}"
        used.add(label)
        labels[c["id"]] = label

    weights = _weights(ds)
    queue = deque(_plan_batches(ds, chosen, weights))
    log.info(
        "Split plan: %d countries in %d batches (%s)",
        len(chosen),
        len(queue),
        ", ".join(str(len(b)) for b in queue),
    )

    finished = 0
    while queue:
        batch = queue.popleft()
        progress(
            f"Batch {finished + 1} of {finished + 1 + len(queue)}: cutting {len(batch)} "
            f"countr{'y' if len(batch) == 1 else 'ies'} (keep this running)"
        )
        try:
            _run_batch(ds, batch, labels, out_dir, progress)
        except (osmium_tool.OsmiumError, MemoryError) as e:
            if len(batch) < 2:
                raise
            # Too big for this machine (usually out of memory): split it in two balanced halves and retry.
            log.warning(
                "Batch of %d countries failed (%s). Retrying as two smaller batches.",
                len(batch),
                getattr(e, "message", None) or type(e).__name__,
            )
            first, second = _balance(batch, weights, 2)
            queue.appendleft(second)
            queue.appendleft(first)
            continue
        finished += 1

    files = []
    for c in chosen:
        p = out_dir / f"{labels[c['id']]}.osm.pbf"
        if p.is_file():
            files.append(
                {"name": p.name, "path": str(p), "size": p.stat().st_size, "country": c["name"]}
            )
    return {"folder": str(out_dir), "files": files}


def _check_refs(path: Path, out_txt: Path) -> tuple[int, int]:
    """Run `osmium check-refs -r -i` (C++) and read its report. Returns (ways, relations) that are affected.

    Collected into the module-level sets by the caller; this only runs the tool and writes its output to `out_txt`.
    Exit code 1 just means "some references are missing", which is exactly what we expect here.
    """
    exe = osmium_tool.require()
    env = {**os.environ, "PATH": str(exe.parent) + os.pathsep + os.environ.get("PATH", "")}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        with out_txt.open("wb") as f:
            p = subprocess.run(
                [str(exe), "check-refs", "-r", "-i", str(path)],
                stdout=f,
                stderr=subprocess.STDOUT,
                env=env,
                check=False,
                creationflags=flags,
            )
    except OSError as e:
        raise osmium_tool.OsmiumError(f"Could not start osmium: {e}"[:140]) from e
    if p.returncode not in {0, 1}:
        tail = out_txt.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-15:]
        last = tail[-1] if tail else "no message"
        raise osmium_tool.OsmiumError(f"osmium stopped: {last}"[:150], "\n".join(tail))
    return p.returncode, 0


def _clean_file(path: Path, work: Path, label: str) -> dict:
    """Clean one extracted file in place: remove ways that reference missing nodes, drop relation members that point
    to ways / relations that no longer exist, and remove relations left with no members.
    Node members of relations are left as they are.

    Fast path: `osmium check-refs` reports every missing reference in C++, so Python never walks the nodes or the
    ways. Python only reads the relations (to decide which ones survive) and rewrites the few that changed.
    """
    import osmium

    refs = work / f"{label}.refs.txt"
    code, _ = _check_refs(path, refs)

    bad: set[int] = set()  # ways with at least one node missing from the file
    gone_ways: set[int] = set()  # ways that relations point to but the file does not hold
    parsed = False
    with refs.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            for what, ref, _in, owner in _REF.findall(line):
                parsed = True
                if owner == "w":
                    bad.add(int(owner_id := ref) if False else int(_owner_id(line, ref)))
                elif what == "w":
                    gone_ways.add(int(ref))
    refs.unlink(missing_ok=True)

    if code == 1 and not parsed:
        msg = "osmium check-refs found problems but its report could not be read"
        raise osmium_tool.OsmiumError(msg)

    gone_ways |= bad

    rel = osmium.osm.RELATION
    info: dict[
        int, tuple[int, tuple, bool]
    ] = {}  # relation id -> (kept non-relation members, children, lost a way)
    for o in osmium.FileProcessor(str(path), rel):
        kept, children, lost = 0, [], False
        for m in o.members:
            t = str(m.type)[0]
            if t == "r":
                children.append(m.ref)
            elif t == "w":
                if m.ref in gone_ways:
                    lost = True
                else:
                    kept += 1
            else:
                kept += 1
        info[o.id] = (kept, tuple(children), lost)

    alive = {rid for rid, (kept, _c, _l) in info.items() if kept > 0}
    # a relation holding only relations lives while one of them lives
    pending = {rid: ch for rid, (kept, ch, _l) in info.items() if kept == 0 and ch}
    grew = True
    while grew and pending:
        grew = False
        for rid in [r for r, ch in pending.items() if any(c in alive for c in ch)]:
            alive.add(rid)
            del pending[rid]
            grew = True
    dead = set(info) - alive
    fixed = {
        rid for rid in alive
        if info[rid][2] or any(c not in alive for c in info[rid][1])
    }  # fmt: skip
    stats = {
        "ways_removed": len(bad),
        "relations_removed": len(dead),
        "relations_fixed": len(fixed),
    }
    if not (bad or dead or fixed):
        return stats

    ids = work / f"{label}.rm.txt"
    ids.write_text(
        "\n".join([*(f"w{i}" for i in sorted(bad)), *(f"r{i}" for i in sorted(dead | fixed))]),
        encoding="utf-8",
    )
    cut = work / f"{label}.cut.osm.pbf"
    osmium_tool.run(["removeid", "-i", str(ids), str(path), "-o", str(cut), "--overwrite"])
    if fixed:
        mod = work / f"{label}.mod.osm.pbf"
        with osmium.SimpleWriter(str(mod)) as writer:
            for o in osmium.FileProcessor(str(path), rel):
                if o.id not in fixed:
                    continue
                members = [
                    (str(m.type)[0], m.ref, m.role)
                    for m in o.members
                    if str(m.type)[0] == "n"
                    or ((m.ref not in gone_ways) if str(m.type)[0] == "w" else m.ref in alive)
                ]
                writer.add_relation(o.replace(members=members))
        osmium_tool.run(["merge", str(cut), str(mod), "-o", str(path), "--overwrite"])
    else:
        cut.replace(path)
    return stats


@router.post("/technical/osm/split")
async def split_osm(body: SplitBody):
    ds = _dataset(body.token)
    _need_osmium()
    return _start_job(ds, "split", lambda progress: _split(ds, body, progress))
