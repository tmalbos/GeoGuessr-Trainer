"""replay.py — Fetch, clean and compress round replay event streams."""

import math

import httpx

REPLAY_URL = "https://www.geoguessr.com/api/v4/replays/{user_id}/{game_token}/{round_number}"

# ── Start-of-replay cleanup ─────────────────────────────────────────────────
# GeoGuessr leaks the previous round / the player's setup into the first moments of a replay.
SAME_PLACE_M = 5.0  # two pano positions this close are "the same place"
SETUP_WINDOW_MS = 1500  # leaked setup events only count if they happen before this
SETUP_MAP_CENTER = (11.901182, 9.33603)  # the default minimap position the setup leaks
SETUP_CENTER_TOL = 1e-3


async def fetch_replay(
    http_client: httpx.AsyncClient,
    user_id: str,
    game_token: str,
    round_number: int,
) -> list[dict]:
    r = await http_client.get(
        REPLAY_URL.format(user_id=user_id, game_token=game_token, round_number=round_number),
    )
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return r.json()


def compress_replay(events: list[dict]) -> list[dict]:
    """Rewrite absolute (epoch ms) timestamps as milliseconds relative to
    this replay's own first event. Collapses consecutive PanoPov/PanoZoom
    into deduped 'Deliberating' markers (no payload), except a PanoPov at
    heading=0, pitch=-89 (north + ground, the align hotkey), which is kept
    as a single 'Aligning' event. Strips panoId from PanoPosition.
    Everything else (MapPosition, MapZoom, MapDisplay, PinPosition,
    GuessWithLatLng, ...) is kept verbatim aside from the relative timestamp.
    """
    if not events:
        return []

    anchor_ms = events[0]["time"]
    compressed: list[dict] = []

    for ev in events:
        rel_time_ms = ev["time"] - anchor_ms
        etype = ev["type"]

        if etype == "PanoPov":
            payload = ev.get("payload") or {}
            if payload.get("heading") == 0 and payload.get("pitch") == -89:
                compressed.append({"time": rel_time_ms, "type": "Aligning"})
                continue

        if etype in {"PanoPov", "PanoZoom"}:
            if compressed and compressed[-1]["type"] == "Deliberating":
                continue
            compressed.append({"time": rel_time_ms, "type": "Deliberating"})
            continue

        payload = dict(ev.get("payload") or {})
        if etype == "PanoPosition":
            payload.pop("panoId", None)

        compressed.append({"time": rel_time_ms, "type": etype, "payload": payload})

    return compressed


def _meters_between(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371000.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def _is_setup_center(payload: dict) -> bool:
    lat, lng = payload.get("lat"), payload.get("lng")
    return (
        lat is not None
        and lng is not None
        and abs(lat - SETUP_MAP_CENTER[0]) < SETUP_CENTER_TOL
        and abs(lng - SETUP_MAP_CENTER[1]) < SETUP_CENTER_TOL
    )


def _is_setup_event(ev: dict) -> bool:
    payload = ev.get("payload") or {}
    kind = ev["type"]
    if kind == "Deliberating":
        return True
    if kind == "MapDisplay":
        return payload.get("isActive") is False
    if kind == "MapZoom":
        return payload.get("zoom") == 1
    if kind == "MapPosition":
        return _is_setup_center(payload)
    return False


def clean_replay_start(events: list[dict], real_lat: float, real_lng: float) -> list[dict]:
    """Undo GeoGuessr's start-of-replay contamination. Run on compressed events, before saving.

    1. The first PanoPosition at time 0 is forced onto the real location.
    2. Every other event at time 0 is dropped (nobody can act at exactly 0).
    3. If the next PanoPosition is within SAME_PLACE_M of the real location it is the same
       position, not a step: keep one event at time 0 with the value of the second.
    4. Setup events before SETUP_WINDOW_MS are dropped: Deliberating, MapDisplay off, MapZoom 1
       and the default-position MapPosition.
    """
    out = [dict(ev) for ev in events]
    first = next((ev for ev in out if ev["time"] == 0 and ev["type"] == "PanoPosition"), None)

    # 1. start exactly on the real location
    if first is not None:
        payload = first.get("payload") or {}
        if payload.get("lat") != real_lat or payload.get("lng") != real_lng:
            first["payload"] = {**payload, "lat": real_lat, "lng": real_lng}

    # 2. nothing but PanoPositions can happen at time 0
    out = [ev for ev in out if ev["time"] != 0 or ev["type"] == "PanoPosition"]

    # 3. a "step" to the same place is the same position
    if first is not None:
        i = next(i for i, ev in enumerate(out) if ev is first)
        j = next((k for k in range(i + 1, len(out)) if out[k]["type"] == "PanoPosition"), None)
        if j is not None:
            nxt = out[j].get("payload") or {}
            if (
                nxt.get("lat") is not None
                and nxt.get("lng") is not None
                and _meters_between(nxt["lat"], nxt["lng"], real_lat, real_lng) <= SAME_PLACE_M
            ):
                first["payload"] = dict(nxt)
                del out[j]

    # 4. leaked environment setup in the first moments
    return [ev for ev in out if not (ev["time"] < SETUP_WINDOW_MS and _is_setup_event(ev))]


def steps_from_replay(compressed_events: list[dict]) -> int:
    """Number of distinct pano positions visited during the round."""
    return sum(1 for ev in compressed_events if ev["type"] == "PanoPosition")


def time_sec_from_replay(compressed_events: list[dict]) -> int:
    """Round duration, derived from the last event's relative timestamp."""
    if not compressed_events:
        return 0
    return round(max(ev["time"] for ev in compressed_events) / 1000)


def had_movement(compressed_events: list[dict]) -> bool:
    return steps_from_replay(compressed_events) > 1


def had_pan_or_zoom(compressed_events: list[dict]) -> bool:
    return any(ev["type"] in {"Deliberating", "Aligning"} for ev in compressed_events)
