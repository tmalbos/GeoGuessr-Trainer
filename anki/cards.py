"""
cards.py — Generación de tarjetas Anki para GeoGuessr.
"""

import re
import requests
import unicodedata
import yaml

from anki.anki_connect import _invoke
from db.mongo import _db

DECK         = "GeoGuessr"
MODEL_BASIC  = "Básico (teclear la respuesta)"
MODEL_SINGLE = "Single Choice"
SEPARATOR    = "<br>"

_session = requests.Session()
_session.headers.update({"User-Agent": "GeoGuessr-Anki/1.0"})
_cache: dict[str, dict] = {}


def _remove_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def _pascal(name: str) -> str:
    name = _remove_accents(name)
    return "".join(
        w.capitalize()
        for w in re.split(r"[\s\-]+", name.strip())
        if w.lower() not in ("y", "and", "de", "america")
    )


def _rest(cca2: str) -> dict:
    """Busca por código ISO — nunca falla por variantes de nombre."""
    key = cca2.lower()
    if key in _cache:
        return _cache[key]
    try:
        r = _session.get(
            f"https://restcountries.com/v3.1/alpha/{cca2}",
            params={"fields": "translations,capital,car,tld,cca2,flags"},
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            entry = data[0] if isinstance(data, list) else data
            _cache[key] = entry
            return entry
    except Exception:
        pass
    return {}


def _download_flag(flag_url: str, filename: str) -> bool:
    """Descarga la imagen de bandera al media de Anki via AnkiConnect."""
    try:
        r = _session.get(flag_url, timeout=10)
        if r.status_code != 200:
            return False
        import base64
        data_b64 = base64.b64encode(r.content).decode()
        _invoke("storeMediaFile", filename=filename, data=data_b64)
        return True
    except Exception:
        return False


def _build_roads_yaml(roads: dict) -> str:
    """Convierte el subdocumento 'roads' de MongoDB a YAML indentado."""
    raw = yaml.dump({"roads": roads}, allow_unicode=True, sort_keys=False).strip()
    return f"<pre>{raw}</pre>"


def build_roads_note(country_name: str, cca2: str) -> dict | None:
    """
    Consulta geo_signals por country_code (cca2 en mayúsculas).
    Si existe el campo 'roads', devuelve la nota lista para agregar.
    Si no, devuelve None.
    """
    doc = _db["geo_signals"].find_one(
        {"country_code": cca2.upper()},
        {"roads": 1, "_id": 0},
    )

    if not doc or not doc.get("roads"):
        return None

    pc = f"Pais::{_pascal(country_name)}"
    return {
        "model": "GeoGuessr Roads",
        "fields": {
            "Pregunta": f"¿Cómo son las lineas de las rutas de {country_name}?",
            "YamlData": _build_roads_yaml(doc["roads"]),
        },
        "tags": [pc, "Basico::Rutas::Lineas"],
    }


def build_license_plates_note(country_name: str, cca2: str) -> dict | None:
    doc = _db["geo_signals"].find_one(
        {"country_code": cca2.upper()},
        {"license_plates": 1, "_id": 0},
    )

    if not doc or not doc.get("license_plates"):
        return None

    raw = yaml.dump(
        {"license_plates": doc["license_plates"]},
        allow_unicode=True,
        sort_keys=False,
    ).strip()

    pc = f"Pais::{_pascal(country_name)}"
    return {
        "model": "GeoGuessr License Plates",
        "fields": {
            "Pregunta": f"¿Cómo son las matrículas de {country_name}?",
            "YamlData": f"<pre>{raw}</pre>",
        },
        "tags": [pc, "Basico::Matriculas"],
    }


def build_notes(country_code: str) -> list[dict]:
    data = _rest(country_code)
    country_name = data.get("translations", {}).get("spa", {}).get("common", country_code)

    if not data:
        print(f"  [WARN] No se encontró data para '{country_name}' en REST Countries")
        return []

    capital  = _remove_accents((data.get("capital") or [None])[0])
    side     = data.get("car", {}).get("side", "").lower()
    tld      = (data.get("tld") or [None])[0]
    flag_url = data.get("flags", {}).get("png") or data.get("flags", {}).get("svg", "")
    cca2     = data.get("cca2", "").lower()

    pc    = f"Pais::{_pascal(country_name)}"
    tags  = [pc]
    notes = []

    # 1. Bandera
    if flag_url and cca2:
        filename = f"flag_{cca2}.png"
        if _download_flag(flag_url, filename):
            notes.append({
                "model":  MODEL_BASIC,
                "fields": {
                    "Anverso": f'¿De qué país es esta bandera?{SEPARATOR}<img src="{filename}" style="max-width:300px;">',
                    "Reverso": country_name,
                },
                "tags": tags + ["Basico::Bandera"],
            })

    # 2. Capital
    if capital:
        notes.append({
            "model":  MODEL_BASIC,
            "fields": {
                "Anverso": f"Cual es la capital de {country_name}?",
                "Reverso": capital,
            },
            "tags": tags + ["Basico::Capital"],
        })

    # 3. Lado de conducción
    if side:
        side_label = "1" if side == "left" else "2"

        notes.append({
            "model":  MODEL_SINGLE,
            "fields": {
                "Pregunta": f"¿De qué lado se maneja en {country_name}?",
                "Opciones": f"Izquierda{SEPARATOR}Derecha",
                "Respuesta": side_label,
                "Mezclar": "false",
            },
            "tags": tags + ["Basico::LadoConduccion"],
        })

    # 4. Dominio
    if tld:
        notes.append({
            "model":  MODEL_BASIC,
            "fields": {
                "Anverso": f"""Que pais tiene este dominio?{SEPARATOR}{tld.upper()}""",
                "Reverso": country_name,
            },
            "tags": tags + ["Basico::Dominio"],
        })

    # 5. Líneas de ruta
    if cca2:
        roads_note = build_roads_note(country_name, cca2)
        if roads_note:
            notes.append(roads_note)

    # 6. Matrículas
    if cca2:
        plates_note = build_license_plates_note(country_name, cca2)
        if plates_note:
            notes.append(plates_note)

    return notes
