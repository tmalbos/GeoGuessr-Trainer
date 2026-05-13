import json

import requests

BASE_URL_V3 = "https://www.geoguessr.com/api/v3"
BASE_URL_V4 = "https://www.geoguessr.com/api/v4"


class CookieExpiredError(Exception):
    pass


def extract_game_id(raw: str) -> str:
    raw = raw.strip()
    if "geoguessr.com" in raw:
        return raw.rstrip("/").split("/")[-1]
    return raw


class GeoguessrClient:
    def __init__(self, ncfa_cookie: str):
        self._headers = {
            "Cookie": f"_ncfa={ncfa_cookie}",
            "User-Agent": "Mozilla/5.0",
        }

    def _get(self, url: str) -> requests.Response:
        r = requests.get(url, headers=self._headers, timeout=10)
        if r.status_code == 401:
            raise CookieExpiredError()
        return r

    def fetch_game(self, game_token: str) -> dict:
        r = self._get(f"{BASE_URL_V3}/games/{game_token}")
        if r.status_code == 404:
            raise ValueError(f"Juego '{game_token}' no encontrado.")
        r.raise_for_status()
        return r.json()

    def fetch_feed_entries(self, count: int = 26) -> list[dict]:
        """Devuelve lista de entradas con challenge_token, is_daily y date_str."""
        r = self._get(f"{BASE_URL_V4}/feed/private?count={count}")
        r.raise_for_status()

        entries = []
        for entry in r.json().get("entries", []):
            try:
                parsed = json.loads(entry.get("payload", ""))
            except (json.JSONDecodeError, TypeError):
                continue

            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                payload = item.get("payload", item)
                token = payload.get("challengeToken")
                if not token:
                    continue
                if token in {e["challenge_token"] for e in entries}:
                    continue

                date_str = entry.get("time", "")[:10]

                entries.append(
                    {
                        "challenge_token": token,
                        "is_daily": payload.get("isDailyChallenge", False),
                        "date_str": date_str,
                    }
                )

        return entries

    def fetch_game_token(self, challenge_token: str) -> str | None:
        """Resuelve un challengeToken al game token real."""
        url = f"{BASE_URL_V3}/results/highscores/{challenge_token}?friends=true&limit=1&minRounds=5"
        r = self._get(url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            return None
        return items[0].get("game", {}).get("token")

    def fetch_daily_game_token(self, date_str: str, user_id: str) -> str | None:
        """Resuelve el game token del reto diario para un usuario y fecha dados."""
        url = (
            f"{BASE_URL_V3}/challenges/daily-challenges/leaderboard/free/country?dateStr={date_str}"
        )
        r = self._get(url)
        if r.status_code == 404:
            return None
        r.raise_for_status()

        entries = r.json().get("entries", [])
        match = next((e for e in entries if e.get("userId") == user_id), None)
        if not match:
            return None

        return match.get("game", {}).get("token")


# Mantener estas funciones sueltas para compatibilidad con menu_insert() actual
def fetch_game(game_id: str, ncfa_cookie: str) -> dict:
    return GeoguessrClient(ncfa_cookie).fetch_game(game_id)
