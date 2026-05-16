"""
geo_enrich.py
Enriquece coordenadas con jerarquía geográfica completa.
Todas las llamadas de red son async; eco_enrich (CPU/disco) se corre en executor.
"""

import asyncio

import httpx

from src.core.regions import get_override

_country_cache: dict[str, dict] = {}
_client = httpx.AsyncClient(
    headers={"User-Agent": "GeoGuessr-Analyzer/1.0"},
    timeout=8,
)

# Nominatim: máximo 1 request simultáneo
_nominatim_sem = asyncio.Semaphore(1)


def _hemisphere(lat: float) -> str:
    if lat > 10:
        return "North"
    elif lat < -10:
        return "South"
    else:
        return "Equatorial"


async def _nominatim(lat: float, lon: float) -> dict:
    async with _nominatim_sem:
        try:
            r = await _client.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={
                    "lat": lat,
                    "lon": lon,
                    "format": "json",
                    "zoom": 10,
                    "accept-language": "es",
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


async def _rest_countries(country_code: str) -> dict:
    if not country_code:
        return {"continent": "", "subregion": ""}
    if country_code in _country_cache:
        return _country_cache[country_code]
    try:
        r = await _client.get(
            f"https://restcountries.com/v3.1/alpha/{country_code}",
            params={"fields": "continents,subregion"},
        )
        data = r.json()
        if isinstance(data, list):
            data = data[0]
        result = {
            "continent": data.get("continents", [""])[0],
            "subregion": data.get("subregion", ""),
        }
    except Exception:
        result = {"continent": "", "subregion": ""}
    _country_cache[country_code] = result
    return result


async def enrich(lat: float | None, lon: float | None) -> dict:
    empty = {
        "lat": lat,
        "lng": lon,
        "hemisphere": "",
        "continent": "",
        "subregion": "",
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

    nominatim_data = await _nominatim(lat, lon)

    override_cca2 = get_override(
        nominatim_data["country_code"], nominatim_data["state"]
    ) or get_override(nominatim_data["country_code"], nominatim_data["city"])

    effective_cca2 = override_cca2 or nominatim_data["country_code"]
    country_data = await _rest_countries(nominatim_data["country_code"])

    # eco_enrich es CPU/disco — se corre en el executor para no bloquear el loop
    loop = asyncio.get_running_loop()
    from src.core.eco_enrich import lookup as eco_lookup

    eco = await loop.run_in_executor(None, eco_lookup, lat, lon)

    return {
        "lat": lat,
        "lng": lon,
        "hemisphere": _hemisphere(lat),
        "continent": country_data["continent"],
        "subregion": country_data["subregion"],
        "country_code": effective_cca2,
        "country": nominatim_data["country"],
        "state": nominatim_data["state"],
        "city": nominatim_data["city"],
        "realm": eco["realm"],
        "biome": eco["biome"],
        "ecoregion": eco["ecoregion"],
    }


async def enrich_all(coords: list[tuple[float | None, float | None]]) -> list[dict]:
    """Enriquece todas las coordenadas respetando el semáforo de Nominatim."""
    return await asyncio.gather(*[enrich(lat, lon) for lat, lon in coords])
