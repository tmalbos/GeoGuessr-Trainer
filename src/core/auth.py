"""auth.py — Manejo de cookie _ncfa de GeoGuessr.
Intenta extraerla automáticamente via Playwright.
Si no puede, pide que el usuario la ingrese manualmente.
"""

import os
import pathlib

from src.i18n.lang import translate

COOKIE_FILE = "geoguessr_cookie.txt"

browsers = [
    {
        "name": "Brave",
        "user_data": os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data"),
        "executable": r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    },
    {
        "name": "Chrome",
        "user_data": os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data"),
        "executable": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    },
    {
        "name": "Edge",
        "user_data": os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data"),
        "executable": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    },
]

try:
    from playwright.async_api import async_playwright

    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False


def load_cookie() -> str | None:
    if pathlib.Path(COOKIE_FILE).exists():
        with pathlib.Path(COOKIE_FILE).open(encoding="utf-8") as f:
            cookie = f.read().strip()
        if cookie:
            return cookie

    return None


def save_cookie(cookie: str) -> None:
    pathlib.Path(COOKIE_FILE).write_text(cookie, encoding="utf-8")


import shutil
import sqlite3
import tempfile


async def extract_cookie_playwright() -> str | None:
    """Tries to read _ncfa directly from the browser's cookie SQLite DB.
    Avoids launching a browser entirely (no lock conflicts, no headless detection).
    Note: Chrome 80+ encrypts cookie values with DPAPI on Windows.
          This works if the cookie 'value' column is populated (some builds still store it).
    """
    for cfg in browsers:
        if not pathlib.Path(cfg["user_data"]).exists():
            continue

        for sub in ["Default/Network/Cookies", "Default/Cookies"]:
            cookie_db = os.path.join(cfg["user_data"], sub.replace("/", os.sep))
            if not pathlib.Path(cookie_db).exists():
                continue

            with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
                shutil.copy2(cookie_db, tmp.name)
                tmp_path = tmp.name

            try:
                con = sqlite3.connect(tmp_path)
                cur = con.execute(
                    "SELECT value FROM cookies "
                    "WHERE host_key LIKE '%geoguessr.com' AND name = '_ncfa'",
                )
                row = cur.fetchone()
                con.close()
                if row and row[0]:
                    return row[0]
            except Exception as e:
                print(f"  [SQLite] Error reading {cookie_db}: {e}")
            finally:
                pathlib.Path(tmp_path).unlink()

    # Fallback: Playwright with non-headless + copied profile
    if not PLAYWRIGHT_OK:
        return None

    return await _extract_via_playwright_fallback()


async def _extract_via_playwright_fallback() -> str | None:
    cfg = next(
        (
            b
            for b in browsers
            if pathlib.Path(b["user_data"]).exists() and pathlib.Path(b["executable"]).exists()
        ),
        None,
    )
    if not cfg:
        return None

    # Copy profile to avoid lock conflicts
    tmp_dir = tempfile.mkdtemp()
    default_src = os.path.join(cfg["user_data"], "Default")
    shutil.copytree(default_src, os.path.join(tmp_dir, "Default"))

    try:
        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir=tmp_dir,
                executable_path=cfg["executable"],
                headless=False,  # avoid detection
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = await ctx.new_page()
            await page.goto("https://www.geoguessr.com", timeout=15000)
            await page.wait_for_load_state("load", timeout=10000)  # not networkidle
            cookies = await ctx.cookies("https://www.geoguessr.com")
            ncfa = next((c["value"] for c in cookies if c["name"] == "_ncfa"), None)
            await ctx.close()
            return ncfa
    except Exception as e:
        print(f"  [Playwright] Error: {e}")
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def refresh_cookie() -> str | None:
    """Llamado cuando la cookie expira.
    Intenta renovarla automáticamente antes de pedir intervención manual.
    """
    cookie = await extract_cookie_playwright()

    if cookie:
        save_cookie(cookie)

        return cookie

    raise RuntimeError(translate("  ⚠️  Could not refresh the cookie"))
