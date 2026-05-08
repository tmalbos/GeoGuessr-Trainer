"""
GeoGuessr Analyzer — Punto de entrada principal.
"""

import os
import sys
from core.auth     import load_cookie, prompt_new_cookie, refresh_cookie
from core.api      import fetch_game, extract_game_id, CookieExpiredError
from core.analyzer import parse_and_display
from core.eco_enrich import init as eco_init, is_ready, load_error
from core.stats    import print_analysis, available_levels
from db.mongo      import MONGO_OK

MIN_ROUNDS = 10


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def menu_insert():
    cookie = load_cookie()
    if not cookie:
        print("\n⚠️  No hay cookie guardada. Ingresá una primero.")
        cookie = prompt_new_cookie()
        if not cookie:
            return

    raw = input("\nPegá el link o game ID de tu partida:\n> ").strip()
    game_id = extract_game_id(raw)
    if not game_id:
        print("❌ Link o ID inválido.")
        return

    print(f"\n🔍 Buscando juego: {game_id} ...")
    try:
        game_data = fetch_game(game_id, cookie)
    except CookieExpiredError:
        new_cookie = refresh_cookie()
        if new_cookie and game_id:
            # reintenta con la cookie nueva
            try:
                game_data = fetch_game(game_id, new_cookie)
                if game_data:
                    parse_and_display(game_data, game_id)
            except CookieExpiredError:
                print("\n❌ No se pudo renovar la cookie.")
        return

    if game_data:
        parse_and_display(game_data, game_id)

    # Volver al menú automáticamente sin pedir Enter
    clear()


def menu_analysis(levels: list[tuple]):
    options = {str(i+1): lv for i, lv in enumerate(levels)}

    while True:
        clear()
        print("\n  ── Análisis ─────────────────────────────")
        for key, (level, label, n) in options.items():
            print(f"  [{key}] {label}  ({n} rondas)")
        print("  [0] Volver")

        choice = input("\n> ").strip()
        if choice == "0":
            return
        if choice in options:
            level, label, _ = options[choice]
            clear()
            print_analysis(level)
            input("\n  Presioná Enter para continuar...")
        else:
            print("  Opción no válida.")


def menu_change_cookie():
    print("\n── Cambiar cookie ──────────────────────────")
    prompt_new_cookie()
    clear()


def main():
    clear()
    eco_init()
    print("\n🌍  GeoGuessr Analyzer")
    print("─" * 30)
    if not MONGO_OK:
        print("  ℹ️  MongoDB no disponible — los datos no se guardarán.")
        print("     Instalá pymongo y asegurate de tener Mongo corriendo.\n")

    err = load_error()
    if err is None and not is_ready():
        print("  🌿  Cargando ecoregiones en background...")
    elif err is not None:
        print(f"  ⚠️  Ecoregiones no disponibles: {err}")

    while True:
        levels = available_levels(MIN_ROUNDS) if MONGO_OK else []

        print()
        print("  [1] Insertar partida")
        if levels:
            print("  [2] Análisis de datos")
        print("  [3] Cambiar cookie")

        choice = input("\n> ").strip()

        if choice == "1":
            menu_insert()
        elif choice == "2" and levels:
            menu_analysis(levels)
            clear()
        elif choice == "3":
            menu_change_cookie()
        else:
            print("  Opción no válida.")
            continue

        clear()
        print("\n🌍  GeoGuessr Analyzer")
        print("─" * 30)


if __name__ == "__main__":
    main()

# 1FuBcJtqwwU8Z5Ht
