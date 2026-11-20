"""prefix_levels.py — One map per depth for a drawn clue whose area names start with a code (phone area codes...).

The drawn parquet stays the source. For every depth worth showing, a parquet of its own is written next to it
(reg_phonenumbers_00003.d1.parquet): the areas whose names start the same way are UNITED into one area, named and
coloured by that start, so the card just loads the file of the depth it shows.

A name may list overlay areas with "/" (41/42): each part is read on its own and the first one decides the group.
A depth only counts when it tells apart more areas than the one before, so a filler start shared by every name
(0xx) never gives a depth of its own. Keep the colours in step with nameColors in frontend prefixColors.js.
"""

import colorsys
import re
from pathlib import Path

import geopandas as gpd
import shapely

SATURATION = 0.65
EXTRA_COLUMNS = {"extra": "", "extra_blur": False, "extra_max_height": 0}


def _parts(name) -> list[str]:
    return [p for p in (re.sub(r"[\W_]+", "", a).upper() for a in str(name or "").split("/")) if p]


def _key(name: str, depth: int) -> str:
    parts = _parts(name)
    return parts[0][:depth] if parts else ""


def _prefixes(name: str, depth: int) -> set[str]:
    return {p[:depth] for p in _parts(name)}


def depth_options(names: list[str]) -> list[int]:
    """The depths worth offering, lowest first (empty when the names can't be told apart by their start)."""
    longest = max((len(p) for n in names for p in _parts(n)), default=0)
    out: list[int] = []
    seen = 1
    for depth in range(1, longest + 1):
        count = len({x for n in names for x in _prefixes(n, depth)})
        if count > seen:
            out.append(depth)
            seen = count
    return out


def level_name(geo_name: str, depth: int) -> str:
    return f"{Path(geo_name).stem}.d{depth}.parquet"


def _hex(hue: float, light: float) -> str:
    rgb = colorsys.hls_to_rgb(hue / 360, light / 100, SATURATION)
    return "#" + "".join(f"{round(v * 255):02x}" for v in rgb)


def build_levels(src: Path, folder: Path, geo_name: str) -> dict[int, str]:
    """Writes the parquet of every depth into `folder`. Returns {depth: file name} ({} when there is nothing to do)."""
    gdf = gpd.read_parquet(src)
    if not {"name", "color"} <= set(gdf.columns):
        return {}
    keys = gdf["name"].astype(str)
    names = list(dict.fromkeys(keys))
    options = depth_options(names)
    if not options:
        return {}
    geoms = {
        n: shapely.union_all(shapely.make_valid(gdf.geometry[keys == n].to_numpy())) for n in names
    }
    first = {n: gdf[keys == n].iloc[0] for n in names}
    has_extra = "extra" in gdf.columns
    top = sorted({_key(n, options[0]) for n in names})

    out: dict[int, str] = {}
    for depth in options:
        groups: dict[str, list[str]] = {}
        for n in names:
            groups.setdefault(_key(n, depth), []).append(n)
        inside: dict[str, list[str]] = {}
        for key in sorted(groups):
            inside.setdefault(key[: options[0]], []).append(key)
        rows = []
        for key in sorted(groups):
            members = groups[key]
            siblings = inside[key[: options[0]]]
            hue = round((top.index(key[: options[0]]) * 137.5) % 360)
            light = (
                55
                if len(siblings) < 2
                else round(75 - 50 * siblings.index(key) / (len(siblings) - 1))
            )
            single = len(members) == 1 and depth == options[-1]
            row = {
                "name": members[0] if single or not key else key,
                "color": _hex(hue, light),
                "geometry": shapely.union_all([geoms[m] for m in members]),
            }
            if has_extra:
                for col, default in EXTRA_COLUMNS.items():
                    row[col] = first[members[0]].get(col, default) if single else default
            rows.append(row)
        name = level_name(geo_name, depth)
        gpd.GeoDataFrame(rows, geometry="geometry", crs=4326).to_parquet(
            folder / name, compression="zstd"
        )
        out[depth] = name
    return out
