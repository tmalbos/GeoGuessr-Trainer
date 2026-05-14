from .base import Note


class CapitalNote(Note):
    def __init__(self, country_data: dict):
        Note.__init__(self, country_data)
        self.MODEL = "Básico (teclear la respuesta)"
        self.capital = (country_data.get("capital") or [None])[0]

    # ── Normal: given the capital, name the country ───────────────────────
    def model(self) -> str:
        return self.MODEL

    def fields(self) -> dict | None:
        return {
            "Anverso": f"¿De qué país es capital {self.capital}?",
            "Reverso": self._remove_accents(self.country_name),
        }

    def tags(self) -> list[str]:
        return ["Basico::Capital"]

    # ── Inverse: given the country, name the capital ───────────────────────
    def inverse_model(self) -> str:
        return self.MODEL

    def inverse_fields(self) -> dict | None:
        return {
            "Anverso": f"¿Cuál es la capital de {self.country_name}?",
            "Reverso": self._remove_accents(self.capital),
        }

    def inverse_tags(self) -> list[str]:
        return ["Inverso::Basico::Capital"]
