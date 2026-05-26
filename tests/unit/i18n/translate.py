"""Tests for translate — string lookup and substitution."""

from i18n.lang import translate


def test_translate_returns_english_with_substitution_when_no_locale_loaded():
    """translate() returns the English key with kwargs applied in EN mode (no locale loaded)."""
    assert translate("Found {count} rounds", count=5) == "Found 5 rounds"
