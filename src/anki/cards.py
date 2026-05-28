"""cards.py — Generación de tarjetas Anki para GeoGuessr."""

import base64
import importlib
import inspect
import pkgutil
import re
import unicodedata

import httpx

import src.anki.notes as notes_pkg
from src.anki.anki_connect import AnkiConnectClient
from src.anki.notes.base import Note
from src.db.db import DbAdapter


def _remove_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _pascal(name: str) -> str:
    name = _remove_accents(name)
    return "".join(
        w.capitalize()
        for w in re.split(r"[\s\-]+", name.strip())
        if w.lower() not in {"y", "and", "de", "america"}
    )


async def _download_flag(flag_url: str, http_client: httpx.AsyncClient) -> str | None:
    r = await http_client.get(flag_url, timeout=10)

    if r.status_code != 200:
        msg = "Couldn't download flag"
        raise RuntimeError(msg)

    return base64.b64encode(r.content).decode()


_cache: dict[str, dict] = {}


async def build_notes(
    country_code: str,
    db: DbAdapter,
    http_client: httpx.AsyncClient,
    anki_client: AnkiConnectClient,
) -> list[dict]:

    async def _rest(cca2: str) -> dict:
        key = cca2.lower()
        if key in _cache:
            return _cache[key]
        try:
            r = await http_client.get(
                f"https://restcountries.com/v3.1/alpha/{cca2}",
                params={"fields": "translations,capital,car,tld,cca2,flags"},
            )
            if r.status_code == 200:
                data = r.json()
                entry = data[0] if isinstance(data, list) else data
                _cache[key] = entry
                return entry
        except Exception:
            pass
        return {}

    data = await _rest(country_code)
    if not data:
        print(f"  [WARN] No REST data for '{country_code}'")
        return []

    cca2 = data.get("cca2", "").lower()
    flag_url = data["flags"]["png"]
    flag = await _download_flag(flag_url, http_client)
    flag_filename = f"flag_{cca2}.png"
    await anki_client.store_media(flag_filename, flag)

    country_name = data.get("translations", {}).get("spa", {}).get("common", country_code)

    geo = await db.fetch_country_geo_signals(cca2.upper())  # replaces find_documents

    country_data = {
        **data,
        "flag_filename": flag_filename,
        "country_name": country_name,
        "pascal_tag": f"Pais::{_pascal(country_name)}",
        "roads": geo.get("roads"),
        "license_plates": geo.get("license_plates"),
    }

    note_classes = [
        cls
        for _, module_name, _ in pkgutil.iter_modules(notes_pkg.__path__)
        for _, cls in inspect.getmembers(
            importlib.import_module(f"src.anki.notes.{module_name}"),
            inspect.isclass,
        )
        if issubclass(cls, Note) and cls is not Note
    ]

    return [
        {**note, "tags": note["tags"] + [country_data["pascal_tag"]]}
        for cls in note_classes
        if (note := cls(country_data, http_client=http_client, anki_client=anki_client).note())
        is not None
    ]
