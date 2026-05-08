"""
auth.py — Manejo de cookie _ncfa de GeoGuessr.
Intenta extraerla automáticamente via Playwright.
Si no puede, pide que el usuario la ingrese manualmente.
"""

import os
from core.config import COOKIE_FILE

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False


INSTRUCTIONS = """
╔══════════════════════════════════════════════════════════════╗
║           CÓMO OBTENER TU COOKIE DE GEOGUESSR               ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  1. Abrí Chrome y entrá a https://geoguessr.com             ║
║  2. Iniciá sesión con tu cuenta                              ║
║  3. Presioná F12 → Application → Cookies                    ║
║  4. Buscá "_ncfa" y copiá su valor                          ║
║                                                              ║
║  ⚠️  Esta cookie es tu sesión activa. No la compartas.       ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""


def load_cookie() -> str | None:
    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE) as f:
            cookie = f.read().strip()
        if cookie:
            return cookie
    return None


def save_cookie(cookie: str):
    with open(COOKIE_FILE, "w") as f:
        f.write(cookie)


def extract_cookie_playwright() -> str | None:
    """
    Busca Brave, Chrome o Edge (en ese orden), abre con el perfil real
    del usuario y extrae _ncfa de GeoGuessr.
    """
    if not PLAYWRIGHT_OK:
        return None

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

    cfg = next((b for b in browsers
                if os.path.exists(b["user_data"]) and os.path.exists(b["executable"])), None)

    if not cfg:
        print("  [Playwright] No se encontró ningún browser compatible.")
        return None

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=cfg["user_data"],
                executable_path=cfg["executable"],
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = ctx.new_page()
            page.goto("https://www.geoguessr.com", timeout=15000)
            page.wait_for_load_state("networkidle", timeout=10000)
            cookies = ctx.cookies("https://www.geoguessr.com")
            ncfa = next((c["value"] for c in cookies if c["name"] == "_ncfa"), None)
            ctx.close()
            return ncfa
    except Exception as e:
        print(f"  [Playwright] Error: {e}")
        return None
    except Exception as e:
        print(f"  [Playwright] No se pudo extraer la cookie: {e}")
        return None


def prompt_new_cookie() -> str:
    """
    Intenta extraer la cookie automáticamente.
    Si falla, pide al usuario que la ingrese manualmente.
    """
    print("\n  Intentando extraer cookie automáticamente...", end="\r")
    cookie = extract_cookie_playwright()

    if cookie:
        save_cookie(cookie)
        print("  ✅ Cookie extraída y guardada automáticamente.          ")
        return cookie

    # Fallback manual
    print("  ⚠️  No se pudo extraer automáticamente.                   ")
    print(INSTRUCTIONS)
    cookie = input("Pegá tu cookie _ncfa y presioná Enter:\n> ").strip()
    if cookie:
        save_cookie(cookie)
        print("✅ Cookie guardada.")
    return cookie


def refresh_cookie() -> str | None:
    """
    Llamado cuando la cookie expira.
    Intenta renovarla automáticamente antes de pedir intervención manual.
    """
    print("\n  🔄 Cookie expirada. Intentando renovar automáticamente...")
    cookie = extract_cookie_playwright()
    if cookie:
        save_cookie(cookie)
        print("  ✅ Cookie renovada automáticamente.")
        return cookie
    print("  ⚠️  No se pudo renovar. Ingresala manualmente.")
    return prompt_new_cookie()