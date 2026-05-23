"""
GeoGuessr Analyzer — Punto de entrada principal.
"""

import asyncio
import os

from dotenv import load_dotenv

from src.anki.generator import wait_for_anki
from src.core.api import CookieExpiredError
from src.core.app_context import AppContext
from src.core.auth import load_cookie, prompt_new_cookie, refresh_cookie
from src.core.calculator import analyze
from src.core.eco_enrich import init as eco_init
from src.core.eco_enrich import is_ready, load_error
from src.core.printer import print_analysis as print_stats_analysis
from src.core.stats import available_levels, build_groups, load_rounds
from src.core.sync import sync_from_feed

MIN_ROUNDS = 10


def clear():
    os.system("cls" if os.name == "nt" else "clear")


async def menu_insert(db):
    cookie = load_cookie()
    if not cookie:
        print("\n⚠️  No hay cookie guardada. Ingresá una primero.")
        cookie = prompt_new_cookie()
        if not cookie:
            return

    if not await wait_for_anki():
        return

    try:
        await sync_from_feed(cookie, db=db)
    except CookieExpiredError:
        print("\n🔄 Cookie expirada, intentando renovar...")
        new_cookie = refresh_cookie()
        if new_cookie:
            try:
                await sync_from_feed(new_cookie, db=db)
            except CookieExpiredError:
                print("\n❌ No se pudo renovar la cookie.")

    input("\n  Presioná Enter para continuar...")
    clear()


async def menu_analysis(levels: list[tuple], db):
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
            rounds = await load_rounds(db)
            geo_level = None if level == "general" else level
            result = analyze(rounds, geo_level)
            groups = build_groups(rounds, geo_level)
            print_stats_analysis(result, level, groups)
            input("\n  Presioná Enter para continuar...")
        else:
            print("  Opción no válida.")


def menu_change_cookie():
    print("\n── Cambiar cookie ──────────────────────────")
    prompt_new_cookie()
    clear()


async def main():
    clear()
    load_dotenv()
    eco_init()

    app_ctx = AppContext(
        db_dsn=os.environ.get("PG_DSN", ""),
        ncfa_cookie=os.environ.get("NCFA_COOKIE", ""),
    )
    await app_ctx.init()
    db = app_ctx.db_adapter

    db_live = await asyncio.wait_for(db.check_connection(), timeout=2.0)

    print("\n🌍  GeoGuessr Analyzer")
    print("─" * 30)

    if not db_live:
        print("  ℹ️  PostgreSQL no disponible — los datos no se guardarán.")
        print("     Asegurate de tener Postgres corriendo y PG_DSN configurado.\n")

    err = load_error()
    if err is None and not is_ready():
        print("  🌿  Cargando ecoregiones en background...")
    elif err is not None:
        print(f"  ⚠️  Ecoregiones no disponibles: {err}")

    while True:
        levels = await available_levels(db, MIN_ROUNDS) if db_live else []

        print()
        print("  [1] Sincronizar partidas")
        if levels:
            print("  [2] Análisis de datos")
        print("  [3] Cambiar cookie")

        choice = input("\n> ").strip()

        if choice == "1":
            await menu_insert(db)
        elif choice == "2" and levels:
            await menu_analysis(levels, db)
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
    asyncio.run(main())
