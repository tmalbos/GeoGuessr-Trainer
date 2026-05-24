"""
generator.py — Orquesta la generación de tarjetas Anki para una partida.
"""

import httpx

from src.anki.anki_connect import AnkiConnectClient
from src.anki.cards import build_notes
from src.db.db import DbAdapter

DECK = "GeoGuessr"


async def generate_cards_for_game(
    rounds: list[dict],
    db: DbAdapter,
    anki_client: AnkiConnectClient,
    http_client: httpx.AsyncClient,
) -> list[str]:
    """
    Genera tarjetas para los países vistos en las rondas.
    Devuelve lista de errores (vacía si todo fue bien).
    """
    seen = {r["real_geo"]["country_code"] for r in rounds if r["real_geo"].get("country_code")}

    if not seen:
        print("\n  ⚠️  No se encontraron países en las rondas.")
        return []

    await anki_client.ensure_deck(DECK)

    created = 0
    skipped = 0
    errors = []

    for code in seen:
        notes = await build_notes(code, db=db, http_client=http_client, anki_client=anki_client)

        for note in notes:
            if await anki_client.note_exists(DECK, note["tags"]):
                skipped += 1
                continue
            try:
                result = await anki_client.add_note(
                    deck=DECK,
                    model=note["model"],
                    fields=note["fields"],
                    tags=note["tags"],
                )
                if result:
                    created += 1
                else:
                    skipped += 1
            except Exception as e:
                if "duplicate" in str(e).lower():
                    skipped += 1
                else:
                    errors.append(f"({code}): {e}")

    print(f"  ✅ Tarjetas creadas: {created}  |  Ya existían: {skipped}")
    return errors


async def wait_for_anki(anki_client: AnkiConnectClient) -> bool:
    """Verifica que Anki esté corriendo. Bloquea hasta que el usuario lo abra."""
    import asyncio

    if await anki_client.is_running():
        return True

    print("\n  ⚠️  Anki no está abierto")
    print("  Abrí Anki y asegurate de que AnkiConnect esté instalado")

    while True:
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: input("  Presioná Enter cuando Anki esté listo... ")
        )
        if await anki_client.is_running():
            print("  ✅ Anki detectado")
            return True
        print("\n  ⚠️  Anki sigue sin responder")
