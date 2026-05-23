import asyncio

from src.anki.generator import generate_cards_for_game
from src.core.geo_enrich import enrich_all
from src.db.db import save_game


def fmt_time(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def fmt_location(geo: dict) -> str:
    parts = [geo.get("city"), geo.get("state"), geo.get("country")]
    return ", ".join(p for p in parts if p) or "Desconocido"


async def process_game(game_data: dict, game_id: str) -> list[str]:
    """
    Enriquece, muestra, guarda y genera cards para una partida.
    Devuelve lista de errores de Anki.
    """
    rounds_raw = game_data.get("rounds", [])
    player_data = game_data.get("player", {})
    guesses = player_data.get("guesses", [])
    map_name = game_data.get("mapName", "unknown")
    played_at = rounds_raw[0].get("startTime") if rounds_raw else None

    if not rounds_raw:
        print("\n⚠️  No se encontraron rondas en este juego.")
        return []

    real_coords = [(rnd["lat"], rnd["lng"]) for rnd in rounds_raw]
    guess_coords = [(g.get("lat"), g.get("lng")) for g in guesses]

    print(f"  [{game_id}] Enriqueciendo datos geográficos...")
    real_enriched, guess_enriched = await asyncio.gather(
        enrich_all(real_coords),
        enrich_all(guess_coords),
    )

    print("\n" + "═" * 65)
    print(f"  RESULTADOS — {map_name}")
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

        print(f"\n  Ronda {i}")
        print(f"  {'─' * 55}")
        print(f"  📍 Lugar real  : {fmt_location(real_geo)}")
        print(f"  🎯 Tu guess    : {fmt_location(guess_geo)}")
        print(f"  ⏱ Tiempo      : {fmt_time(time_sec)}")
        print(f"  ⭐ Puntaje     : {score_pts:,} / 5,000")
        print(
            f"  📏 Distancia   : {dist_km:,} km" if dist_km is not None else "  📏 Distancia   : —"
        )
        print(f"  👣 Pasos       : {steps}")

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
            }
        )

    cant = len(guesses)
    avg_dist = round(total_dist_km / cant, 1) if cant else 0

    print("\n" + "═" * 65)
    print(
        f"  RESUMEN: {cant} rondas  |  "
        f"Puntaje total: {total_score:,} / 25,000  |  "
        f"Distancia promedio: {avg_dist:,} km"
    )
    print("═" * 65 + "\n")

    await save_game(
        {
            "game_id": game_id,
            "challenge_token": game_data.get("challenge_token"),
            "is_daily": game_data.get("is_daily"),
            "played_at": played_at,
            "map_name": map_name,
            "rounds": rounds_to_save,
        }
    )

    print("  🃏 Generando tarjetas Anki...")
    return await generate_cards_for_game(rounds_to_save)
