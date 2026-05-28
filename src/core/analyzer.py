import asyncio

import httpx

from src.anki.anki_connect import AnkiConnectClient
from src.anki.generator import generate_cards_for_game
from src.core.geo_enrich import GeoEnrichClient
from src.db.db import DbAdapter
from src.i18n.lang import translate


def fmt_time(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def fmt_location(geo: dict) -> str:
    parts = [geo.get("city"), geo.get("state"), geo.get("country")]
    return ", ".join(p for p in parts if p) or translate("Unknown")


async def process_game(
    game_data: dict,
    game_id: str,
    db: DbAdapter,
    geo_client: GeoEnrichClient,
    anki_client: AnkiConnectClient,
    http_client: httpx.AsyncClient,
) -> list[str]:
    """Enriquece, muestra, guarda y genera cards para una partida.
    Devuelve lista de errores de Anki.
    """
    rounds_raw = game_data.get("rounds", [])
    player_data = game_data.get("player", {})
    guesses = player_data.get("guesses", [])
    map_name = game_data.get("mapName", "unknown")
    played_at = rounds_raw[0].get("startTime") if rounds_raw else None

    if not rounds_raw:
        print("\n" + translate("⚠️  No rounds found in this game."))
        return []

    real_coords = [(rnd["lat"], rnd["lng"]) for rnd in rounds_raw]
    guess_coords = [(g.get("lat"), g.get("lng")) for g in guesses]

    print(translate("  [{game_id}] Enriching geographic data...", game_id=game_id))
    real_enriched, guess_enriched = await asyncio.gather(
        geo_client.enrich_all(real_coords),
        geo_client.enrich_all(guess_coords),
    )

    print("\n" + "═" * 65)
    print(translate("  RESULTS — {map_name}", map_name=map_name))
    print("═" * 65)

    rounds_to_save = []
    total_score = 0
    total_dist_km = 0.0

    for i, (_rnd, guess) in enumerate(zip(rounds_raw, guesses, strict=False), start=1):
        score_pts = guess.get("roundScoreInPoints", 0)
        dist_km = (
            round(guess["distanceInMeters"] / 1000, 1) if guess.get("distanceInMeters") else None
        )
        time_sec = guess.get("time")
        steps = guess.get("stepsCount", 0)

        real_geo = real_enriched[i - 1]
        guess_geo = guess_enriched[i - 1]

        total_score += score_pts or 0
        total_dist_km += dist_km or 0.0

        print("\n  " + translate("Round {i}", i=i))
        print(f"  {'─' * 55}")
        print(translate("  📍 Real location : {loc}", loc=fmt_location(real_geo)))
        print(translate("  🎯 Your guess    : {loc}", loc=fmt_location(guess_geo)))
        print(translate("  ⏱ Time          : {t}", t=fmt_time(time_sec)))
        print(translate("  ⭐ Score         : {pts} / 5,000", pts=f"{score_pts:,}"))
        dist_str = f"{dist_km:,} km" if dist_km is not None else "—"
        print(translate("  📏 Distance      : {dist}", dist=dist_str))
        print(translate("  👣 Steps         : {steps}", steps=steps))

        rounds_to_save.append(
            {
                "game_id": game_id,
                "round_number": i,
                "real_geo": real_geo,
                "guess_geo": guess_geo,
                "score": score_pts,
                "distance_km": dist_km,
                "steps": steps,
                "time_sec": time_sec,
            },
        )

    cant = len(guesses)
    avg_dist = round(total_dist_km / cant, 1) if cant else 0

    print("\n" + "═" * 65)
    print(
        translate(
            "  SUMMARY: {cant} rounds  |  Total score: {total_score} / 25,000  |  Average distance: {avg_dist} km",
            cant=cant,
            total_score=f"{total_score:,}",
            avg_dist=f"{avg_dist:,}",
        ),
    )
    print("═" * 65 + "\n")

    await db.save_game(
        {
            "game_id": game_id,
            "challenge_token": game_data.get("challenge_token"),
            "is_daily": game_data.get("is_daily"),
            "played_at": played_at,
            "map_name": map_name,
            "rounds": rounds_to_save,
        },
    )

    print(translate("  🃏 Generating Anki cards..."))
    return await generate_cards_for_game(
        rounds_to_save,
        db=db,
        anki_client=anki_client,
        http_client=http_client,
    )
