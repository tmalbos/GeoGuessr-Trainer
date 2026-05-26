"""
geo_enrich.py
Enriquece coordenadas con jerarquía geográfica completa.
Todas las llamadas de red son async; eco_enrich (CPU/disco) se corre en executor.
"""

import asyncio

import httpx

from src.core.regions import get_override


class GeoEnrichClient:
    """Geo-enrichment client that wraps Nominatim and eco_lookup."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        self._client = http_client
        self._semaphore = semaphore or asyncio.Semaphore(1)
        self.ecoregion_gdf = None

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

    async def enrich(self, lat: float | None, lon: float | None) -> dict:
        empty = {
            "lat": lat,
            "lng": lon,
            "country_code": "",
            "country": "",
            "state": "",
            "city": "",
            "realm": "",
            "biome": "",
            "ecoregion": "",
        }
        if lat is None or lon is None:
            return empty

        nominatim_data = await self._nominatim(lat, lon)

        override_cca2 = get_override(
            nominatim_data["country_code"], nominatim_data["state"]
        ) or get_override(nominatim_data["country_code"], nominatim_data["city"])

        effective_cca2 = override_cca2 or nominatim_data["country_code"]

        # eco_enrich es CPU/disco — se corre en el executor para no bloquear el loop
        loop = asyncio.get_running_loop()
        from src.core.eco_enrich import lookup as eco_lookup

        gdf = self.ecoregion_gdf
        eco = await loop.run_in_executor(None, eco_lookup, lat, lon, gdf)

        return {
            "lat": lat,
            "lng": lon,
            "country_code": effective_cca2,
            "country": nominatim_data["country"],
            "state": nominatim_data["state"],
            "city": nominatim_data["city"],
            "realm": eco["realm"],
            "biome": eco["biome"],
            "ecoregion": eco["ecoregion"],
        }

    async def enrich_all(self, coords: list[tuple[float | None, float | None]]) -> list[dict]:
        """Enriquece todas las coordenadas respetando el semáforo de Nominatim."""
        return await asyncio.gather(*[self.enrich(lat, lon) for lat, lon in coords])
