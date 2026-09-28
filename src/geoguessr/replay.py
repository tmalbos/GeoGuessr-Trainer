"""replay.py — Fetch and compress round replay event streams."""

import httpx

REPLAY_URL = "https://www.geoguessr.com/api/v4/replays/{user_id}/{game_token}/{round_number}"


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
    into deduped 'Deliberating' markers (no payload). Strips panoId from
    PanoPosition. Everything else (MapPosition, MapZoom, MapDisplay,
    PinPosition, GuessWithLatLng, ...) is kept verbatim aside from the
    relative timestamp.
    """
    if not events:
        return []

    anchor_ms = events[0]["time"]
    compressed: list[dict] = []

    for ev in events:
        rel_time_ms = ev["time"] - anchor_ms
        etype = ev["type"]

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
    return any(ev["type"] == "Deliberating" for ev in compressed_events)
