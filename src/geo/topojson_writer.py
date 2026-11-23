"""topojson_writer.py — Write shapely geometries as TopoJSON straight from numpy arrays.

Replaces "geometry -> GeoJSON text -> JSON parse -> mapshaper / topojson package -> text" for the exports. Everything
here is vectorised (no Python loop over vertices) and the file is streamed to disk.

Format: integer coordinates on a 1e-7 degree grid (OSM itself stores coordinates at exactly that resolution, about
1 cm), delta-encoded per arc, with the transform in the header. Shared borders are stored once:

  1. every vertex gets an id (equal coordinates -> equal id);
  2. a vertex is a junction when the paths through it do not all have the same two neighbours;
  3. paths are cut at junctions (rings without any are cut at their smallest vertex so identical rings still match);
  4. an arc is identified by its first two vertices, in whichever direction gives the smaller key. Two arcs that start
     the same way between junctions are the same arc, because nothing branches off in between.

Polygons are written with the exterior ring clockwise and holes counter-clockwise. Supports points, lines and
polygons (and their multi versions).
"""

import json
from pathlib import Path

import numpy as np
import shapely

SCALE = 10_000_000  # grid steps per degree
_OFF = 1 << 31
CHUNK = 200_000  # points formatted per call
FLUSH = 2000  # arcs per file write


def _decompose(geoms: np.ndarray):
    parts, gi = shapely.get_parts(geoms, return_index=True)
    keep = ~shapely.is_empty(parts)
    parts, gi = parts[keep], gi[keep]
    tid = shapely.get_type_id(parts)
    poly = np.flatnonzero(tid == 3)
    rings, rp = shapely.get_rings(parts[poly], return_index=True)
    ring_part = poly[rp]
    line = np.flatnonzero(tid == 1)
    paths = np.concatenate([rings, parts[line]])
    part_of = np.concatenate([ring_part, line])
    closed = np.concatenate([np.ones(len(rings), bool), np.zeros(len(line), bool)])
    exterior = np.ones(len(rings), bool)
    exterior[1:] = ring_part[1:] != ring_part[:-1]
    hole = np.concatenate([~exterior, np.zeros(len(line), bool)])
    order = np.argsort(part_of, kind="stable")
    return parts, gi, tid, paths[order], part_of[order], closed[order], hole[order]


def _clean(paths, closed, hole):
    """Quantised vertices of every path: rings without the closing vertex, no repeated vertices, no paths that
    collapsed. Returns (points int64 (N, 2), path index per point, valid mask per path).
    """
    count = len(paths)
    xy, ci = shapely.get_coordinates(paths, return_index=True)
    q = np.rint(xy * SCALE).astype(np.int64)
    n = np.bincount(ci, minlength=count)
    last = np.cumsum(n) - 1
    mask = np.ones(len(q), bool)
    mask[last[closed & (n > 0)]] = False
    q, ci = q[mask], ci[mask]
    mask = np.ones(len(q), bool)
    mask[1:] = ~((q[1:] == q[:-1]).all(1) & (ci[1:] == ci[:-1]))
    q, ci = q[mask], ci[mask]
    n = np.bincount(ci, minlength=count)
    start = np.cumsum(n) - n
    end = start + n - 1
    wrap = closed & (n > 1)
    wrap[wrap] = (q[end[wrap]] == q[start[wrap]]).all(1)
    mask = np.ones(len(q), bool)
    mask[end[wrap]] = False
    q, ci = q[mask], ci[mask]
    n = np.bincount(ci, minlength=count)
    ok = n >= np.where(closed, 3, 2)
    exterior = np.maximum.accumulate(np.where(~hole, np.arange(count), -1))
    ok &= ok[exterior]  # a hole needs its exterior
    mask = ok[ci]
    return q[mask], ci[mask], ok


def _orient(q, pr, closed, hole):
    """Reverse the rings that run the wrong way (exterior clockwise, holes counter-clockwise)."""
    count = len(closed)
    n = np.bincount(pr, minlength=count)
    o = np.cumsum(n) - n
    local = np.arange(len(q)) - o[pr]
    nxt = o[pr] + (local + 1) % n[pr]
    base = q[o][pr]
    a = (q - base).astype(np.float64)
    b = (q[nxt] - base).astype(np.float64)
    area = np.bincount(pr, weights=a[:, 0] * b[:, 1] - b[:, 0] * a[:, 1], minlength=count)
    rev = closed & np.where(hole, area < 0, area > 0)
    if not rev.any():
        return q
    return q[o[pr] + np.where(rev[pr], n[pr] - 1 - local, local)]


def _topology(q, pr, closed):
    """(arcs as delta-encoded points, arc start offsets, arc lengths, arc refs per point of the paths, path of each
    arc occurrence). Arc refs are `i`, or `~i` when the path runs the arc backwards.
    """
    count = len(closed)
    total = len(q)
    n = np.bincount(pr, minlength=count)
    o = np.cumsum(n) - n
    local = np.arange(total) - o[pr]
    first = local == 0
    last = local == n[pr] - 1
    shut = closed[pr]

    key = ((q[:, 0] + _OFF).astype(np.uint64) << np.uint64(32)) | (q[:, 1] + _OFF).astype(np.uint64)
    order = np.argsort(key, kind="stable")
    skey = key[order]
    new = np.empty(total, bool)
    new[0] = True
    new[1:] = skey[1:] != skey[:-1]
    pid = np.empty(total, np.int64)
    pid[order] = np.cumsum(new) - 1
    uniq = int(pid.max()) + 1

    idx = np.arange(total)
    nxt = idx + 1
    nxt[last] = np.where(shut[last], o[pr][last], -1)
    prv = idx - 1
    prv[first] = np.where(shut[first], (o + n - 1)[pr][first], -1)
    a = np.where(prv >= 0, pid[prv], -1)
    b = np.where(nxt >= 0, pid[nxt], -1)
    pair = (np.minimum(a, b) + 1) * (uniq + 1) + (np.maximum(a, b) + 1)
    del a, b, nxt, prv, idx
    ps = pair[order]
    gstart = np.flatnonzero(new)
    junction = (np.minimum.reduceat(ps, gstart) != np.maximum.reduceat(ps, gstart))[pid]
    del ps, pair, skey, order, new
    junction[(first | last) & ~shut] = True

    pj = pr[junction]
    has = np.bincount(pj, minlength=count) > 0
    loose = closed & ~has  # rings with no junction are cut at their smallest vertex
    if loose.any():
        low = np.minimum.reduceat(key, o)
        cand = np.flatnonzero((key == low[pr]) & loose[pr])
        _, pick = np.unique(pr[cand], return_index=True)
        junction[cand[pick]] = True
    del key

    ji = np.flatnonzero(junction)
    _, pick = np.unique(pr[ji], return_index=True)
    shift = np.zeros(count, np.int64)
    shift[pr[ji[pick]]] = local[ji[pick]]
    gather = o[pr] + (local + shift[pr]) % n[pr]
    q, pid, junction = q[gather], pid[gather], junction[gather]
    del gather, local, first, last, shut

    m = n + closed
    mo = np.cumsum(m) - m
    size = int(m.sum())
    extra = (mo + m - 1)[closed]
    body = np.ones(size, bool)
    body[extra] = False
    qe = np.empty((size, 2), np.int64)
    pe = np.empty(size, np.int64)
    je = np.empty(size, bool)
    qe[body], pe[body], je[body] = q, pid, junction
    qe[extra], pe[extra], je[extra] = q[o[closed]], pid[o[closed]], True
    path_e = np.repeat(np.arange(count), m)
    del q, pid, junction

    bound = np.flatnonzero(je)
    same = path_e[bound[:-1]] == path_e[bound[1:]]
    s, e = bound[:-1][same], bound[1:][same]
    k1 = pe[s] * (uniq + 1) + pe[s + 1]
    k2 = pe[e] * (uniq + 1) + pe[e - 1]
    rev = k2 < k1
    _, rep, inv = np.unique(np.minimum(k1, k2), return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    refs = np.where(rev, ~inv, inv)
    arc_path = path_e[s]

    su, eu, ru = s[rep], e[rep], rev[rep]
    length = eu - su + 1
    cs = np.cumsum(length) - length
    step = np.arange(int(length.sum())) - np.repeat(cs, length)
    take = np.where(
        np.repeat(ru, length), np.repeat(eu, length) - step, np.repeat(su, length) + step
    )
    pts = qe[take]
    delta = pts.copy()
    delta[1:] -= pts[:-1]
    delta[cs] = pts[cs]
    return delta, cs, length, refs, arc_path


def _write_arcs(f, delta, cs, length) -> None:
    sep = ""
    buf: list[str] = []
    for c0, n in zip(cs.tolist(), length.tolist(), strict=True):
        text = []
        for a in range(c0, c0 + n, CHUNK):
            flat = delta[a : min(a + CHUNK, c0 + n)].reshape(-1).tolist()
            text.append(("[%d,%d]," * (len(flat) // 2)) % tuple(flat))
        buf.append("[" + "".join(text)[:-1] + "]")
        if len(buf) >= FLUSH:
            f.write(sep + ",".join(buf))
            sep, buf = ",", []
    if buf:
        f.write(sep + ",".join(buf))


def write_topojson(path: Path, geoms, props: list[dict], name: str) -> None:
    """Write `geoms` (one shapely geometry per feature) with their `props` dicts as one TopoJSON object `name`."""
    geoms = np.asarray(geoms, dtype=object)
    parts, gi, tid, paths, part_of, closed, hole = _decompose(geoms)
    q, pr, ok = _clean(paths, closed, hole)
    new_id = np.cumsum(ok) - 1
    pr = new_id[pr]
    part_of, closed, hole = part_of[ok], closed[ok], hole[ok]

    delta = np.zeros((0, 2), np.int64)
    cs = length = np.zeros(0, np.int64)
    refs_list: list[int] = []
    aoff = [0] * (len(closed) + 1)
    if len(q):
        q = _orient(q, pr, closed, hole)
        delta, cs, length, refs, arc_path = _topology(q, pr, closed)
        refs_list = refs.tolist()
        aoff = np.concatenate(
            [[0], np.cumsum(np.bincount(arc_path, minlength=len(closed)))]
        ).tolist()

    is_point = np.flatnonzero(tid == 0)
    pxy = np.rint(shapely.get_coordinates(parts[is_point]) * SCALE).astype(np.int64)
    prow = {int(p): i for i, p in enumerate(is_point)}
    pstart = np.searchsorted(part_of, np.arange(len(parts))).tolist()
    pend = np.searchsorted(part_of, np.arange(len(parts)), side="right").tolist()
    gstart = np.searchsorted(gi, np.arange(len(geoms))).tolist()
    gend = np.searchsorted(gi, np.arange(len(geoms)), side="right").tolist()
    gtid = shapely.get_type_id(geoms).tolist()

    def path_refs(p: int) -> list[int]:
        return refs_list[aoff[p] : aoff[p + 1]]

    def polygon(part: int) -> list:
        return [path_refs(p) for p in range(pstart[part], pend[part])]

    low = [np.inf, np.inf]
    high = [-np.inf, -np.inf]
    if len(q):
        low = (q.min(0) / SCALE).tolist()
        high = (q.max(0) / SCALE).tolist()
    if len(pxy):
        low = np.minimum(low, pxy.min(0) / SCALE).tolist()
        high = np.maximum(high, pxy.max(0) / SCALE).tolist()

    def geometry(g: int) -> dict:
        t = gtid[g]
        mine = range(gstart[g], gend[g])
        if t in {3, 6}:
            polys = [polygon(x) for x in mine if pend[x] > pstart[x]]
            if not polys:
                return {"type": None}
            return (
                {"type": "Polygon", "arcs": polys[0]}
                if t == 3
                else {"type": "MultiPolygon", "arcs": polys}
            )
        if t in {1, 5}:
            lines = [path_refs(pstart[x]) for x in mine if pend[x] > pstart[x]]
            if not lines:
                return {"type": None}
            return (
                {"type": "LineString", "arcs": lines[0]}
                if t == 1
                else {"type": "MultiLineString", "arcs": lines}
            )
        pts = [pxy[prow[x]].tolist() for x in mine if x in prow]
        if not pts:
            return {"type": None}
        return (
            {"type": "Point", "coordinates": pts[0]}
            if t == 0
            else {"type": "MultiPoint", "coordinates": pts}
        )

    dump = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
    with path.open("w", encoding="utf-8") as f:
        step = repr(1 / SCALE)
        f.write(f'{{"type":"Topology","transform":{{"scale":[{step},{step}],"translate":[0,0]}}')
        if np.isfinite(low).all():
            f.write(',"bbox":' + dump([*low, *high]))
        f.write(',"objects":{' + dump(name) + ':{"type":"GeometryCollection","geometries":[')
        for g in range(len(geoms)):
            geom = geometry(g)
            geom["properties"] = props[g]
            f.write(("," if g else "") + dump(geom))
        f.write(']}},"arcs":[')
        _write_arcs(f, delta, cs, length)
        f.write("]}")
