from pathlib import Path

import aiofiles
import yaml

_translations: dict[str, str] = {}
_current_lang: str = "en"

_LOCALES_DIR = Path(__file__).parents[2] / "locales"
_LANG_CODES: dict[str, str] = {"en": "eng", "es": "spa"}


def _flatten(d: dict, result: dict[str, str] | None = None) -> dict[str, str]:
    """Recursively collect all leaf string values into a flat {en_key: translated} mapping."""
    if result is None:
        result = {}
    for k, v in d.items():
        if isinstance(v, dict):
            _flatten(v, result)
        elif isinstance(v, str):
            result[str(k)] = v
    return result


async def load(lang: str) -> None:
    """Load locale from locales/{lang}.yaml. No-op for 'en'; graceful fallback for unknown langs."""
    global _translations, _current_lang
    if lang == "en":
        _translations = {}
        _current_lang = "en"
        return
    path = _LOCALES_DIR / f"{lang}.yaml"
    if not path.exists():
        _translations = {}
        _current_lang = "en"
        return
    async with aiofiles.open(path, encoding="utf-8") as f:
        contents = await f.read()
    _translations = _flatten(yaml.safe_load(contents))
    _current_lang = lang


def translate(text: str, **kwargs) -> str:
    """Return the translated string for text, or text itself if no translation exists.
    String kwargs are themselves translated before substitution."""
    translated = _translations.get(text, text)
    if kwargs:
        translated_kwargs = {
            k: _translations.get(v, v) if isinstance(v, str) else v for k, v in kwargs.items()
        }
        translated = translated.format(**translated_kwargs)
    return translated


def lang_code() -> str:
    """Return the ISO 639-2 code for the active language (e.g. 'eng', 'spa')."""
    return _LANG_CODES.get(_current_lang, "eng")
