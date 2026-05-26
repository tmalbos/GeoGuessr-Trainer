import json

import httpx

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
    def __init__(self, ncfa_cookie: str, http_client: httpx.AsyncClient | None = None):
        self._ncfa_cookie = ncfa_cookie
        self._client = http_client or httpx.AsyncClient(
            headers={
                "Cookie": f"_ncfa={ncfa_cookie}",
                "User-Agent": "Mozilla/5.0",
            },
            timeout=10,
        )

    async def _get(self, url: str) -> httpx.Response:
        headers = {
            "Cookie": f"_ncfa={self._ncfa_cookie}",
            "User-Agent": "Mozilla/5.0",
        }
        r = await self._client.get(url, headers=headers)
        if r.status_code == 401:
            raise CookieExpiredError()
        return r

    async def fetch_game(self, game_token: str) -> dict:
        r = await self._get(f"{BASE_URL_V3}/games/{game_token}")
        if r.status_code == 404:
            raise ValueError(f"Juego '{game_token}' no encontrado.")
        r.raise_for_status()
        return r.json()

    async def fetch_feed_entries(self, count: int = 26) -> list[dict]:
        r = await self._get(f"{BASE_URL_V4}/feed/private?count={count}")
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

    async def fetch_game_token(self, challenge_token: str) -> str | None:
        url = f"{BASE_URL_V3}/results/highscores/{challenge_token}?friends=true&limit=1&minRounds=5"
        r = await self._get(url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            return None
        return items[0].get("game", {}).get("token")

    async def fetch_daily_game_token(self, date_str: str, user_id: str) -> str | None:
        url = (
            f"{BASE_URL_V3}/challenges/daily-challenges/leaderboard/free/country?dateStr={date_str}"
        )
        r = await self._get(url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        entries = r.json().get("entries", [])
        match = next((e for e in entries if e.get("userId") == user_id), None)
        if not match:
            return None
        return match.get("game", {}).get("token")

    async def aclose(self):
        await self._client.aclose()


async def fetch_game(game_id: str, ncfa_cookie: str) -> dict:
    return await GeoguessrClient(ncfa_cookie).fetch_game(game_id)
