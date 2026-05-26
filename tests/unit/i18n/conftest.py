import pytest

import i18n.lang as _lang_mod


@pytest.fixture(autouse=True)
def reset_lang_state():
    _lang_mod._translations = {}
    _lang_mod._current_lang = "en"
    yield
    _lang_mod._translations = {}
    _lang_mod._current_lang = "en"
