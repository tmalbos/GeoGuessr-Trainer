"""Tests for lang_code — active ISO 639-2 language code."""

from i18n.lang import lang_code


def test_lang_code_returns_eng_when_no_locale_is_loaded():
    """lang_code() reports English ISO 639-2 code when running in default EN mode."""
    assert lang_code() == "eng"
