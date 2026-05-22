from pathlib import Path

import aiofiles
import yaml

_translations: dict = {}
_current_lang: str = "es"

_LOCALES_DIR = Path(__file__).parents[2] / "locales"


async def load(lang: str = "es") -> None:
    global _translations, _current_lang
    path = _LOCALES_DIR / f"{lang}.yaml"

    async with aiofiles.open(path, encoding="utf-8") as f:
        contents = await f.read()

    _translations = yaml.safe_load(contents)
    _current_lang = lang


def translate(key: str, **kwargs) -> str:
    keys = key.split(".")
    value = _translations

    for k in keys:
        if not isinstance(value, dict) or k not in value:
            return key

        value = value[k]

    if kwargs and isinstance(value, str):
        value = value.format(**kwargs)

    return value
