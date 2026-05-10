import requests

BASE_URL = "https://www.geoguessr.com/api/v3"


class CookieExpiredError(Exception):
    pass


def fetch_game(game_id: str, ncfa_cookie: str) -> dict:
    url = f"{BASE_URL}/games/{game_id}"
    headers = {
        "Cookie": f"_ncfa={ncfa_cookie}",
        "User-Agent": "Mozilla/5.0",
    }
    r = requests.get(url, headers=headers, timeout=10)

    if r.status_code == 401:
        raise CookieExpiredError()
    elif r.status_code == 404:
        print(f"\n❌ Juego '{game_id}' no encontrado. Verificá el link.")
        return {}
    elif r.status_code != 200:
        print(f"\n❌ Error HTTP {r.status_code}")
        return {}

    return r.json()


def extract_game_id(raw: str) -> str:
    raw = raw.strip()
    if "geoguessr.com" in raw:
        return raw.rstrip("/").split("/")[-1]
    return raw
