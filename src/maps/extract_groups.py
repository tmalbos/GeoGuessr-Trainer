#!/usr/bin/env python3
"""Usage: python extract_groups.py divisions.svg groups.svg out.csv
Assigns each entry of <g id="Land"> in divisions.svg to the entry of
<g id="Land"> in groups.svg with which it overlaps the most.
Fails (no CSV output) if a division is less than 80% inside its best group.
Supports M L H V Z paths (absolute or relative); errors out on curves/arcs.
"""

import csv
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

from shapely.geometry import Polygon
from shapely.ops import unary_union


def parse_path(d):
    """Supports M L H V Z (absolute and relative). Raises on anything else."""
    if re.search(r"[AaCcQqSsTt]", d):
        raise ValueError("Unsupported path command (curve/arc) in: " + d[:60])
    t = re.findall(r"[MmLlHhVvZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", d)
    subs, cur, cmd, i = [], [], None, 0
    x = y = 0.0
    start = (0.0, 0.0)
    while i < len(t):
        if t[i].isalpha():
            cmd = t[i]
            i += 1
            if cmd in "Zz":
                if len(cur) >= 3:
                    subs.append(cur)
                cur, (x, y) = [], start
            continue
        if cmd in "Mm":
            nx, ny = float(t[i]), float(t[i + 1])
            i += 2
            if cmd == "m":
                nx, ny = x + nx, y + ny
            if len(cur) >= 3:
                subs.append(cur)
            x, y = nx, ny
            start, cur = (x, y), [(x, y)]
            cmd = "L" if cmd == "M" else "l"  # extra pairs after M are lineto
            continue
        if cmd in "Ll":
            nx, ny = float(t[i]), float(t[i + 1])
            i += 2
            x, y = (x + nx, y + ny) if cmd == "l" else (nx, ny)
        elif cmd in "Hh":
            nx = float(t[i])
            i += 1
            x = x + nx if cmd == "h" else nx
        elif cmd in "Vv":
            ny = float(t[i])
            i += 1
            y = y + ny if cmd == "v" else ny
        else:
            raise ValueError("Numbers without a path command in: " + d[:60])
        cur.append((x, y))
    if len(cur) >= 3:
        subs.append(cur)
    return unary_union([Polygon(p).buffer(0) for p in subs])


def load(path):
    land = next(e for e in ET.parse(path).iter() if e.get("id") == "Land")
    return {p.get("id"): parse_path(p.get("d")) for p in land.iter() if p.get("d")}


divisions, groups = load(sys.argv[1]), load(sys.argv[2])

THRESHOLD = 0.8
rows = []
for name, shape in divisions.items():
    group = max(groups, key=lambda g: shape.intersection(groups[g]).area)
    coverage = shape.intersection(groups[group]).area / shape.area
    if coverage < THRESHOLD:
        sys.exit(
            f"ERROR: {name} (bounds {[round(x) for x in shape.bounds]}): best match '{group}' covers only {coverage:.0%} (need {THRESHOLD:.0%}), group bounds {[round(x) for x in groups[group].bounds]}"
        )
    rows.append([group, *name.split(".")])

rows.sort(key=lambda r: [(0, int(v), "") if v.isdigit() else (1, 0, v) for v in r])
n = max(len(r) for r in rows) - 1
# Written directly as UTF-8 (no BOM); don't use shell redirection, PowerShell turns it into UTF-16.
with pathlib.Path(sys.argv[3]).open("w", encoding="utf-8", newline="") as f:
    w = csv.writer(f, lineterminator="\n")
    w.writerow(["GroupName"] + [f"Level{i}" for i in range(1, n + 1)])
    w.writerows(rows)
