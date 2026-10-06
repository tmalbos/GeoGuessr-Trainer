#!/usr/bin/env python3
"""Keep only roads whose 'ref' matches a regex, then simplify with mapshaper.

Usage: python filter_roads.py INPUT OUTPUT REGEX
"""

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile


def main() -> None:
    if len(sys.argv) != 4:
        sys.exit("Usage: filter_roads.py INPUT OUTPUT REGEX")
    src, dst, regex = sys.argv[1:]

    pattern = re.compile(regex)
    data = json.loads(pathlib.Path(src).read_text(encoding="utf-8"))
    kept = [
        {"type": "Feature", "properties": {"ref": ref}, "geometry": f["geometry"]}
        for f in data.get("features", [])
        if pattern.search(ref := str((f.get("properties") or {}).get("ref") or ""))
    ]
    print(f"{len(kept)} de {len(data.get('features', []))} rutas coinciden.", file=sys.stderr)
    if not kept:
        sys.exit("Error: ninguna ruta coincide con la regex.")

    mapshaper = shutil.which("mapshaper")
    if mapshaper is None:
        sys.exit("Error: no se encontró 'mapshaper' en el PATH.")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp) / "filtered.geojson"
        tmp_path.write_text(
            json.dumps({"type": "FeatureCollection", "features": kept}, ensure_ascii=False),
            encoding="utf-8",
        )
        subprocess.run(
            [
                mapshaper,
                str(tmp_path),
                "-simplify",
                "resolution=100000x1600",
                "keep-shapes",
                "-o",
                dst,
                "format=geojson",
            ],
            check=True,
        )
    print(f"Wrote {dst}")


if __name__ == "__main__":
    main()
