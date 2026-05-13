"""
geo_enrich.py
Enriquece coordenadas con jerarquía geográfica completa:
  hemisphere, continent, subregion, country_code, country, state, city

Fuentes:
  - Nominatim (zoom=10): hasta nivel ciudad, en español
  - REST Countries: continente, subregión, nombre oficial
  - Latitud: hemisferio
"""

import threading
import time

import requests

from src.core.eco_enrich import lookup as eco_lookup
from src.core.regions import get_override

_nominatim_lock = threading.Semaphore(1)
_session = requests.Session()
_session.headers.update({"User-Agent": "GeoGuessr-Analyzer/1.0"})

# Cache en memoria para no repetir llamadas a REST Countries por el mismo país
_country_cache: dict[str, dict] = {}


def _hemisphere(lat: float) -> str:
    if lat > 10:
        return "North"
    elif lat < -10:
        return "South"
    else:
        return "Equatorial"


def _nominatim(lat: float, lon: float) -> dict:
    with _nominatim_lock:
        try:
            r = _session.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={
                    "lat": lat,
                    "lon": lon,
                    "format": "json",
                    "zoom": 10,
                    "accept-language": "es",
                },
                timeout=8,
            )
            data = r.json()
            addr = data.get("address", {})

            country_code = addr.get("country_code", "").upper()
            country = addr.get("country", "")
            state = addr.get("state") or addr.get("region") or addr.get("county") or ""
            city = (
                addr.get("city")
                or addr.get("town")
                or addr.get("village")
                or addr.get("municipality")
                or ""
            )

            return {
                "country_code": country_code,
                "country": country,
                "state": state,
                "city": city,
            }
        except Exception:
            return {"country_code": "", "country": "", "state": "", "city": ""}
        finally:
            time.sleep(1)


def _rest_countries(country_code: str) -> dict:
    """
    Llama a REST Countries para obtener continente y subregión.
    Cachea por country_code para no repetir llamadas.
    """
    if not country_code:
        return {"continent": "", "subregion": ""}

    if country_code in _country_cache:
        return _country_cache[country_code]

    try:
        r = _session.get(
            f"https://restcountries.com/v3.1/alpha/{country_code}",
            params={"fields": "continents,subregion"},
            timeout=8,
        )
        data = r.json()

        # REST Countries devuelve lista
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


def enrich(lat: float | None, lon: float | None) -> dict:
    """
    Punto de entrada principal.
    Dado (lat, lon), devuelve jerarquía geográfica completa.

    Retorna:
    {
        "lat":        42.47,
        "lng":        1.46,
        "hemisphere": "north",
        "continent":  "Europe",
        "subregion":  "Southern Europe",
        "country":    "Andorra",
        "state":      "Parròquia d'Encamp",
        "city":       "Encamp",
    }
    """
    empty = {
        "lat": lat,
        "lng": lon,
        "hemisphere": "",
        "continent": "",
        "subregion": "",
        "country": "",
        "state": "",
        "city": "",
        "realm": "",
        "biome": "",
        "ecoregion": "",
    }

    if lat is None or lon is None:
        return empty

    nominatim_data = _nominatim(lat, lon)

    override_cca2 = get_override(
        nominatim_data["country_code"], nominatim_data["state"]
    ) or get_override(nominatim_data["country_code"], nominatim_data["city"])

    effective_cca2 = override_cca2 or nominatim_data["country_code"]
    country_data = _rest_countries(nominatim_data["country_code"])
    eco = eco_lookup(lat, lon)

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


def enrich_parallel(coords: list[tuple[float | None, float | None]]) -> list[dict]:
    """
    Enriquece una lista de (lat, lon) en paralelo.
    Respeta el orden de entrada.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = [None] * len(coords)

    with ThreadPoolExecutor(max_workers=8) as ex:
        future_to_idx = {ex.submit(enrich, lat, lon): idx for idx, (lat, lon) in enumerate(coords)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            results[idx] = future.result()

    return results
