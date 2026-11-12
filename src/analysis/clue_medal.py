"""clue_medal.py — Medal (bronze / silver / gold / platinum) of a clue, from its ratings and region area.

Score (0..10), a rating that is not set counts as 0:
  national   (frequency + ease + reliability) / 3
  regional   (freq_part + area_part + ease + reliability) / 4
             freq_part = each shade's frequency * (n-k)/n (darkest full, lightest 1/n),
                         weighted by that shade's share of the total stain area
             area_part = area_score of the summed stain area share of the country (0 if none)
area_score: share of the country's area, 37-63% -> 10, one point less per 4% further away
            (1-5% -> 1, below 1% -> 0, same on the other side).
Medal: ==10 platinum, >=9 gold, >=6 silver, >=2 bronze, else none.
A regional clue without a measurable region (SVG only, no local geo layers) has area_score 0.
"""

import math

import geopandas as gpd
import pandas as pd
import shapely

from src.analysis.report_actions import area_km2
from src.geo.local_geo import CountryAreaMissingError


def area_score(share_pct: float) -> int:
    """Share of the country (0..100 %) -> 0..10."""
    d = abs(min(100.0, max(0.0, share_pct)) - 50.0)
    if d <= 13:
        return 10
    return max(0, 10 - math.ceil((d - 13) / 4))


def medal_of(score: float) -> str | None:
    if score >= 10 - 1e-9:
        return "platinum"
    if score >= 9:
        return "gold"
    if score >= 6:
        return "silver"
    if score >= 2:
        return "bronze"
    return None


def clue_score(clue: dict, stains: list[float], country_km2: float) -> float:
    """Stains = area (km²) of each stain colour, darkest first (empty = no measurable region)."""
    freq, ease, rel = (clue.get(k) or 0 for k in ("frequency", "ease", "reliability"))
    if clue.get("scope") != "reg":
        return (freq + ease + rel) / 3
    n = len(stains)
    if not n or country_km2 <= 0:
        return (freq + ease + rel) / 4
    total = sum(stains)
    if total <= 0:
        return (freq + ease + rel) / 4
    freq_part = sum(freq * (n - k) / n * (a / total) for k, a in enumerate(stains))
    area_part = area_score(100 * sum(stains) / country_km2)
    return (freq_part + area_part + ease + rel) / 4


def _stain_areas(src, folder, cc: str, clue: dict, override: dict) -> list[tuple[int, float]]:
    """[(shade, km²)] of each stain colour, darkest first. `override` maps a file name to the path to read
    instead (a staged file that is about to replace it). `folder` is the country's clue folder.
    """
    name = clue.get("polygons")
    if name:
        gdf = gpd.read_parquet(override.get(name) or folder / name)
        if "shade" in gdf.columns:
            shades = pd.to_numeric(gdf["shade"], errors="coerce").fillna(0).astype(int)
        else:
            shades = pd.Series(0, index=gdf.index)
        out = []
        for v in sorted(set(shades)):
            geoms = shapely.make_valid(gdf.geometry[shades == v].to_numpy())
            out.append((int(v), area_km2(shapely.union_all(geoms))))
        return out
    name = clue.get("csv")
    if name:
        geom = src._csv(cc, override.get(name) or folder / name)  # noqa: SLF001
        if geom is not None and not geom.is_empty:
            return [(0, area_km2(geom))]
    return []


def medal_for(
    cc: str,
    clue: dict,
    src,
    folder,
    override: dict | None = None,
    country_km2: float | None = None,
):
    """(score, medal) of a clue.

    A regional clue raises CountryAreaMissingError when `country_km2` (the area stored in the
    national parquet, see scripts/convert_geo.py) is missing or not positive; run
    scripts/backfill_country_area.py. National clues never need the area.
    """
    country, stains = country_km2 if country_km2 is not None and country_km2 > 0 else 0.0, []
    if clue.get("scope") == "reg":
        if country <= 0:
            raise CountryAreaMissingError(cc)
        stains = [a for _, a in _stain_areas(src, folder, cc, clue, override or {})]
    score = clue_score(clue, stains, country)
    return round(score, 2), medal_of(score)
