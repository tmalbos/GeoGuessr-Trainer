from .base import Note


class FlagNote(Note):
    def __init__(self, country_data: dict, *, http_client=None, anki_client=None):
        Note.__init__(self, country_data, http_client=http_client, anki_client=anki_client)
        self.SEPARATOR = "<br>"
        self.MODEL = "Básico (teclear la respuesta)"
        self.flag_filename = country_data["flag_filename"]
        self.cca2 = country_data["cca2"].lower()

    def model(self) -> str:
        return self.MODEL

    def fields(self) -> dict | None:
        if not self.flag_filename:
            return None

        return {
            "Anverso": f'¿De qué país es esta bandera?{self.SEPARATOR}<img src="{self.flag_filename}" style="max-width:300px;">',
            "Reverso": self._remove_accents(self.country_name),
        }

    def tags(self) -> list[str]:
        return ["Basico::Bandera"]
