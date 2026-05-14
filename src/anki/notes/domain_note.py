from .base import Note


class DomainNote(Note):
    def __init__(self, country_data: dict):
        Note.__init__(self, country_data)
        self.MODEL = "Básico (teclear la respuesta)"
        self.tld = (country_data.get("tld") or [None])[0]

    # ── Normal: given the TLD, name the country ────────────────────────────
    def model(self) -> str:
        return self.MODEL

    def fields(self) -> dict | None:
        if not self.tld:
            return None
        return {
            "Anverso": f"¿De qué país es el dominio {self.tld.upper()}?",
            "Reverso": self._remove_accents(self.country_name),
        }

    def tags(self) -> list[str]:
        return ["Basico::Dominio"]

    # ── Inverse: given the country, name the TLD ───────────────────────────
    def inverse_model(self) -> str:
        return self.MODEL

    def inverse_fields(self) -> dict | None:
        if not self.tld:
            return None
        return {
            "Anverso": f"¿Cuál es el dominio de {self.country_name}?",
            "Reverso": self.tld.upper(),
        }

    def inverse_tags(self) -> list[str]:
        return ["Inverso::Basico::Dominio"]
