"""service.py — pick a random country and a valid window in it, fetch it, pack it."""

from __future__ import annotations

import random
import time

from src.minigames.index import SIZES_KM, gpkg_path, list_countries, load_index
from src.minigames.window import fetch_window, pack


class NoPlace(Exception):
    """No indexed country has a valid window of the requested size (message is shown to the user)."""


def generate_place(size_km: int, country: str | None = None) -> bytes:
    if size_km not in SIZES_KM:
        msg = f"The window size must be one of {', '.join(map(str, SIZES_KM))} km."
        raise ValueError(msg)
    t0 = time.perf_counter()
    eligible = [
        c["name"] for c in list_countries()["countries"] if c["valid"].get(str(size_km), 0) > 0
    ]
    if country:
        eligible = [c for c in eligible if c.lower() == country.lower()]
    if not eligible:
        msg = f"No country has enough roads for a {size_km} km window. Try a smaller size."
        raise NoPlace(msg)
    random.shuffle(eligible)

    pick = None
    for (
        name
    ) in eligible:  # the counts say there is a window; the loop only guards against a stale count
        pick = load_index(name).pick(size_km)
        if pick:
            break
    if not pick:
        msg = f"No valid {size_km} km window was found. Rebuild the indexes."
        raise NoPlace(msg)
    lat, lon = pick
    t1 = time.perf_counter()

    layers, info = fetch_window(gpkg_path(name), lat, lon, size_km)
    t2 = time.perf_counter()
    meta = {
        "country": name,
        "size_km": size_km,
        "lat": lat,
        "lon": lon,
        "half_m": info["half_m"],
        "timing_ms": {"pick": round((t1 - t0) * 1000), "query": round((t2 - t1) * 1000)},
    }
    return pack(layers, meta)
