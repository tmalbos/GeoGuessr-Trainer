"""confusion.py — Which real→guessed zone mix-ups cost the most km."""

import statistics
from collections import defaultdict

from src.analysis.scoring import MEDIUM_CONFIDENCE_WINDOW


def confusion(zone_rounds: list[dict], level: str, top_n: int = 5) -> list[dict]:
    recent = zone_rounds[-MEDIUM_CONFIDENCE_WINDOW:]
    pairs: dict[tuple, list] = defaultdict(list)
    for r in recent:
        real = (r.get("real_geo") or {}).get(level, "")
        guess = (r.get("guess_geo") or {}).get(level, "")
        d = r.get("distance_km")
        if real and guess and real != guess and d is not None:
            pairs[real, guess].append(d)
    rows = []
    for (real, guess), dists in pairs.items():
        freq = len(dists)
        avg_d = round(statistics.mean(dists), 1)
        rows.append(
            {
                "real": real,
                "guess": guess,
                "freq": freq,
                "avg_km": avg_d,
                "impact": round(freq * avg_d),
            },
        )
    return sorted(rows, key=lambda x: -x["impact"])[:top_n]
