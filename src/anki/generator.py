"""
generator.py — Orquesta la generación de tarjetas Anki para una partida.
"""

from src.anki.anki_connect import add_note, ensure_deck, is_running, note_exists
from src.anki.cards import build_notes

DECK = "GeoGuessr"


def _wait_for_anki():
    if is_running():
        return True

    print("\n  ⚠️  Anki no está abierto")
    print("  Abrí Anki y asegurate de que AnkiConnect esté instalado")

    while True:
        input("  Presioná Enter cuando Anki esté listo... ")

        if is_running():
            print("  ✅ Anki detectado")

            return True

        print("\n  ⚠️  Anki no está abierto")
        print("  Abrí Anki y asegurate de que AnkiConnect esté instalado")


def generate_cards_for_game(rounds: list[dict]):
    seen = set()

    for r in rounds:
        code = r["real_geo"]["country_code"]

        if code:
            seen.add(code)

    if not seen:
        print("\n  ⚠️  No se encontraron países en las rondas.")
        return

    if not _wait_for_anki():
        return

    ensure_deck(DECK)

    created = 0
    skipped = 0

    for code in seen:
        notes = build_notes(code)

        for note in notes:
            if note_exists(DECK, note["tags"]):
                skipped += 1
                continue

            try:
                result = add_note(
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
                    print(f"  ⚠️  Error ({code}): {e}")

    print(f"  ✅ Tarjetas creadas: {created}  |  Ya existían: {skipped}")
