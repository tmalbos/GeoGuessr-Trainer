import i18n.lang as _lang_mod
from i18n.lang import translate


def test_translate_returns_english_with_substitution_when_no_locale_loaded() -> None:
    assert translate("Found {count} rounds", count=5) == "Found 5 rounds"


def test_string_kwargs_are_translated_before_substitution() -> None:
    _lang_mod._translations = {
        "Level: {label}": "Nivel: {label}",
        "Country": "Pais",
    }
    result = translate("Level: {label}", label="Country")
    assert result == "Nivel: Pais"
