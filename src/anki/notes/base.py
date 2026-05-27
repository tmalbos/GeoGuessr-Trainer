import unicodedata
from abc import ABC, abstractmethod

from src.i18n.lang import lang_code


class Note(ABC):
    def __init__(self, country_data: dict, *, http_client=None, anki_client=None):
        self.country_name = country_data["translations"][lang_code()]["common"]
        self._http_client = http_client
        self._anki_client = anki_client

    @staticmethod
    def _remove_accents(text: str) -> str:
        return "".join(
            c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
        )

    @abstractmethod
    def model(self) -> str: ...

    @abstractmethod
    def fields(self) -> dict | None: ...

    @abstractmethod
    def tags(self) -> list[str]: ...

    def inverse_model(self) -> str: ...

    def inverse_fields(self) -> dict | None: ...

    def inverse_tags(self) -> list[str]: ...

    def note(self) -> dict | None:
        fields = self.fields()

        if fields is None:
            return None

        return {"model": self.model(), "fields": fields, "tags": self.tags()}

    def inverse_note(self) -> dict | None:
        inverse_fields = self.inverse_fields()

        if inverse_fields is None:
            return None

        return {
            "model": self.inverse_model(),
            "fields": inverse_fields,
            "tags": self.inverse_tags(),
        }
