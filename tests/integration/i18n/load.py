"""Integration tests for load() — reads real locales/es.yaml from the filesystem."""

import pytest

import i18n.lang as _lang_mod
from i18n.lang import load, translate

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def reset_lang_state():
    _lang_mod._translations = {}
    _lang_mod._current_lang = "en"
    yield
    _lang_mod._translations = {}
    _lang_mod._current_lang = "en"


@pytest.mark.asyncio
async def test_translate_returns_spanish_when_key_exists_in_yaml():
    """After loading the Spanish locale, translate returns the Spanish mapping for a known key."""
    # Arrange
    await load("es")

    # Act
    result = translate("Africa")

    # Assert
    assert result == "África"


@pytest.mark.asyncio
async def test_translate_applies_kwargs_when_key_is_missing_in_spanish_locale():
    """After loading Spanish, translate falls back to English with kwargs applied for unknown keys."""
    # Arrange
    await load("es")

    # Act
    result = translate("Missing key {count}", count=3)

    # Assert
    assert result == "Missing key 3"


@pytest.mark.asyncio
async def test_load_unsupported_lang_does_not_raise_and_falls_back_to_english():
    """load() with an unsupported lang code does not raise and leaves the module in English mode."""
    # Act — must not raise even though locales/xx.yaml does not exist
    await load("xx")

    # Assert — translate returns English passthrough
    assert translate("Hello") == "Hello"


@pytest.mark.asyncio
async def test_language_switches_correctly_at_runtime():
    """Switching from Spanish back to English at runtime resets translations to English passthrough."""
    # Arrange — start in Spanish
    await load("es")
    assert translate("Africa") == "África"

    # Act — switch back to English
    await load("en")

    # Assert — English passthrough restored
    assert translate("Africa") == "Africa"
