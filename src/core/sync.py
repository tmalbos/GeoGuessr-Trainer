from src.core.analyzer import parse_and_display
from src.core.api import GeoguessrClient
from src.core.config import COL_GAMES
from src.db.mongo import find_documents

USER_ID = "68daf785000ba2a268744f99"


def sync_from_feed(ncfa_cookie: str) -> None:
    client = GeoguessrClient(ncfa_cookie)

    print("\n🌍 Obteniendo feed...")
    entries = client.fetch_feed_entries()
    if not entries:
        print("⚠️  No se encontraron partidas en el feed.")
        return

    print(f"   {len(entries)} partida(s) encontradas en el feed.\n")

    saved = find_documents(
        COL_GAMES,
        {"challenge_token": {"$in": [e["challenge_token"] for e in entries]}},
        {"challenge_token": 1, "_id": 0},
    )
    saved_tokens = {doc["challenge_token"] for doc in saved}

    inserted, skipped, not_found, errors = [], [], [], []

    for entry in entries:
        challenge_token = entry["challenge_token"]
        is_daily = entry["is_daily"]
        date_str = entry["date_str"]

        if challenge_token in saved_tokens:
            print(f"  🔁 [{challenge_token}] Ya existe en la base de datos.")
            skipped.append(challenge_token)
            continue

        if is_daily:
            game_token = client.fetch_daily_game_token(date_str, USER_ID)
        else:
            game_token = client.fetch_game_token(challenge_token)

        if not game_token:
            print(
                f"  ⚠️  [{challenge_token}] Sin game token — {'diario' if is_daily else 'challenge'} sin resultados."
            )
            not_found.append(challenge_token)
            continue

        print(f"  🔍 [{game_token}] Procesando...")
        try:
            game_data = client.fetch_game(game_token)
        except ValueError as e:
            print(f"  ❌ [{game_token}] No encontrado: {e}")
            not_found.append(game_token)
            continue
        except Exception as e:
            print(f"  ❌ [{game_token}] Error inesperado: {e}")
            errors.append((game_token, str(e)))
            continue

        game_data["challenge_token"] = challenge_token
        game_data["is_daily"] = is_daily

        parse_and_display(game_data, game_token)
        inserted.append(game_token)

    _print_summary(inserted, skipped, not_found, errors)


def _print_summary(
    inserted: list[str],
    skipped: list[str],
    not_found: list[str],
    errors: list[tuple[str, str]],
) -> None:
    print("\n─────────────────────────────")
    print(f"  ✅ Insertadas    : {len(inserted)}")
    print(f"  🔁 Ya existían   : {len(skipped)}")
    print(f"  🔍 No encontradas: {len(not_found)}")
    print(f"  ❌ Errores       : {len(errors)}")
    if errors:
        for token, msg in errors:
            print(f"      · {token}: {msg}")
    print("─────────────────────────────")
