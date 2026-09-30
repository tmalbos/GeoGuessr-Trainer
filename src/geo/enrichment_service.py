"""enrichment_service.py
Enriquece coordenadas con jerarquía geográfica completa.
Local GeoParquet layers first; Nominatim only as fallback for points/countries not covered locally.
"""

import asyncio
from itertools import starmap

import httpx

from src.geo.region_overrides import get_override


class GeoEnrichClient:
    """Geo-enrichment client: LocalGeo first, Nominatim fallback."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        self._client = http_client
        self._semaphore = semaphore or asyncio.Semaphore(1)
        self.local_geo = None  # set by AppContext once the layers are loaded

    async def _nominatim(self, lat: float, lon: float) -> dict:
        async with self._semaphore:
            try:
                r = await self._client.get(
                    "https://nominatim.openstreetmap.org/reverse",
                    params={
                        "lat": lat,
                        "lon": lon,
                        "format": "json",
                        "zoom": 10,
                        "accept-language": "en",
                    },
                )
                addr = r.json().get("address", {})
                return {
                    "country_code": addr.get("country_code", "").upper(),
                    "country": addr.get("country", ""),
                    "state": addr.get("state") or addr.get("region") or addr.get("county") or "",
                    "city": (
                        addr.get("city")
                        or addr.get("town")
                        or addr.get("village")
                        or addr.get("municipality")
                        or ""
                    ),
                }
            except Exception:
                return {"country_code": "", "country": "", "state": "", "city": ""}
            finally:
                await asyncio.sleep(1)

    async def _nominatim_admin(self, lat: float, lon: float) -> dict:
        data = await self._nominatim(lat, lon)
        override_cca2 = get_override(data["country_code"], data["state"]) or get_override(
            data["country_code"],
            data["city"],
        )
        return {
            "country_code": override_cca2 or data["country_code"],
            "country": data["country"],
            "state": data["state"],
            "subregion": None,  # only available from local data
            "city": data["city"],
        }

    async def enrich(self, lat: float | None, lon: float | None) -> dict:
        empty = {
            "lat": lat,
            "lng": lon,
            "country_code": "",
            "country": "",
            "state": "",
            "subregion": None,
            "city": "",
            "biome": "",
            "area_type": None,
        }
        if lat is None or lon is None:
            return empty

        loop = asyncio.get_running_loop()
        local = await loop.run_in_executor(None, self.local_geo.lookup, lat, lon)

        admin = local["admin"]
        if admin is None:
            admin = await self._nominatim_admin(lat, lon)

        return {
            "lat": lat,
            "lng": lon,
            "country_code": admin["country_code"],
            "country": admin["country"],
            "state": admin["state"],
            "subregion": admin["subregion"],
            "city": admin["city"],
            "biome": local["biome"],
            "area_type": local["area_type"],
        }

    async def enrich_all(self, coords: list[tuple[float | None, float | None]]) -> list[dict]:
        """Enriquece todas las coordenadas (Nominatim, si se usa, respeta el semáforo)."""
        return await asyncio.gather(*list(starmap(self.enrich, coords)))
