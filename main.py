"""
GeoGuessr Analyzer — Punto de entrada principal.
"""

import os

from src.core.api import CookieExpiredError
from src.core.auth import (
    load_cookie,
    prompt_new_cookie,
    refresh_cookie,
)
from src.core.eco_enrich import init as eco_init
from src.core.eco_enrich import is_ready, load_error
from src.core.stats import available_levels, print_analysis
from src.core.sync import sync_from_feed
from src.db.mongo import MONGO_OK

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

    try:
        sync_from_feed(cookie)
    except CookieExpiredError:
        print("\n🔄 Cookie expirada, intentando renovar...")
        new_cookie = refresh_cookie()
        if new_cookie:
            try:
                sync_from_feed(new_cookie)
            except CookieExpiredError:
                print("\n❌ No se pudo renovar la cookie.")

    input("\n  Presioná Enter para continuar...")
    clear()


def menu_analysis(levels: list[tuple]):
    options = {str(i + 1): lv for i, lv in enumerate(levels)}

    while True:
        clear()
        print("\n  ── Análisis ─────────────────────────────")
        for key, (_, label, n) in options.items():
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
        print("  [1] Sincronizar partidas")
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
