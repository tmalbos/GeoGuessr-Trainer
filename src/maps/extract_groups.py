#!/usr/bin/env python3
"""Usage: python extract_groups.py divisions.svg groups.svg > out.csv
Assigns each entry of <g id="Land"> in divisions.svg to the entry of
<g id="Land"> in groups.svg with which it overlaps the most.
Fails (no CSV output) if a division is less than 80% inside its best group.
Only supports absolute M/Z paths (as in your example SVG).
"""

import csv
import re
import sys
import xml.etree.ElementTree as ET

from shapely.geometry import Polygon
from shapely.ops import unary_union


def load(path):
    land = next(e for e in ET.parse(path).iter() if e.get("id") == "Land")
    shapes = {}
    for p in land.iter():
        if p.get("d"):
            polys = []
            for sub in re.split(r"[Zz]", p.get("d")):
                nums = [float(n) for n in re.findall(r"-?\d*\.?\d+(?:e-?\d+)?", sub)]
                pts = list(zip(nums[::2], nums[1::2], strict=False))
                if len(pts) >= 3:
                    polys.append(Polygon(pts).buffer(0))
            shapes[p.get("id")] = unary_union(polys)
    return shapes


divisions, groups = load(sys.argv[1]), load(sys.argv[2])

THRESHOLD = 0.8
rows, errors = [], []
for name, shape in divisions.items():
    group = max(groups, key=lambda g: shape.intersection(groups[g]).area)
    coverage = shape.intersection(groups[group]).area / shape.area
    if coverage < THRESHOLD:
        errors.append(f"{name}: best match '{group}' covers only {coverage:.0%}")
    rows.append([group, *name.split(".")])

if errors:
    sys.exit("ERROR, polygons below %d%% match:\n" % (THRESHOLD * 100) + "\n".join(errors))

n = max(len(r) for r in rows) - 1
w = csv.writer(sys.stdout, lineterminator="\n")
w.writerow(["GroupName"] + [f"Level{i}" for i in range(1, n + 1)])
w.writerows(rows)
