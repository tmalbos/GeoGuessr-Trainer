"""
GeoGuessr Analyzer — Punto de entrada principal.
"""

import asyncio
import os

from dotenv import load_dotenv

from src.anki.generator import wait_for_anki
from src.core.api import CookieExpiredError
from src.core.app_context import AppContext
from src.core.auth import load_cookie, refresh_cookie
from src.core.calculator import analyze
from src.core.printer import print_analysis as print_stats_analysis
from src.core.stats import available_levels, build_groups, load_rounds
from src.core.sync import sync_from_feed

MIN_ROUNDS = 10


def clear():
    os.system("cls" if os.name == "nt" else "clear")


async def menu_insert(db, app_ctx: AppContext):
    cookie = load_cookie()

    if not cookie:
        return

    if not await wait_for_anki(app_ctx.anki_client):
        return

    gg_client = app_ctx.create_geoguessr_client()
    try:
        await app_ctx.ecoregion_ready.wait()
        await sync_from_feed(
            gg_client,
            db=db,
            geo_client=app_ctx.geo_client,
            anki_client=app_ctx.anki_client,
            http_client=app_ctx.http_client,
        )
    except CookieExpiredError:
        print("\n🔄 Cookie expirada, intentando renovar...")
        new_cookie = await refresh_cookie()
        if new_cookie:
            fresh_client = app_ctx.create_geoguessr_client()
            try:
                await sync_from_feed(
                    fresh_client,
                    db=db,
                    geo_client=app_ctx.geo_client,
                    anki_client=app_ctx.anki_client,
                    http_client=app_ctx.http_client,
                )
            except CookieExpiredError:
                print("\n❌ No se pudo renovar la cookie.")
            finally:
                await fresh_client.aclose()
        else:
            print("\n❌ No se pudo renovar la cookie.")
    finally:
        await gg_client.aclose()

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


async def main():
    clear()
    load_dotenv()

    app_ctx = AppContext(db_dsn=os.environ.get("PG_DSN", ""))

    await app_ctx.init()

    db = app_ctx.db_adapter
    db_live = await asyncio.wait_for(db.check_connection(), timeout=2.0)

    print("\n🌍  GeoGuessr Analyzer")
    print("─" * 30)

    if not db_live:
        print("  ℹ️  PostgreSQL no disponible — los datos no se guardarán.")
        print("     Asegurate de tener Postgres corriendo y PG_DSN configurado.\n")

    while True:
        levels = await available_levels(db, MIN_ROUNDS) if db_live else []

        print()
        print("  [1] Sincronizar partidas")
        if levels:
            print("  [2] Análisis de datos")
        print("  [3] Cambiar cookie")

        choice = input("\n> ").strip()

        if choice == "1":
            await menu_insert(db, app_ctx)
        elif choice == "2" and levels:
            await menu_analysis(levels, db)
            clear()
        else:
            print("  Opción no válida.")
            continue

        clear()
        print("\n🌍  GeoGuessr Analyzer")
        print("─" * 30)


if __name__ == "__main__":
    asyncio.run(main())
