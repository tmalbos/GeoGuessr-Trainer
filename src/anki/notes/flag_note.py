import base64

import requests

from src.anki.anki_connect import _invoke

from .base import Note

_session = requests.Session()
_session.headers.update({"User-Agent": "GeoGuessr-Anki/1.0"})


class FlagNote(Note):
    def __init__(self, country_data: dict):
        Note.__init__(self, country_data)
        self.SEPARATOR = "<br>"
        self.MODEL = "Básico (teclear la respuesta)"
        self.flag_url = country_data.get("flags", {}).get("png")
        self.cca2 = country_data["cca2"].lower()

    def _download_flag(self) -> str | None:
        """Downloads the flag and stores it in Anki media"""
        if not self.flag_url:
            return None

        r = _session.get(self.flag_url, timeout=10)

        if r.status_code != 200:
            return None

        data_b64 = base64.b64encode(r.content).decode()
        filename = f"flag_{self.cca2}.png"
        _invoke("storeMediaFile", filename=filename, data=data_b64)

        return filename

    def model(self) -> str:
        return self.MODEL

    def fields(self) -> dict | None:
        filename = self._download_flag()

        if not filename:
            return None

        return {
            "Anverso": f'¿De qué país es esta bandera?{self.SEPARATOR}<img src="{filename}" style="max-width:300px;">',
            "Reverso": self._remove_accents(self.country_name),
        }

    def tags(self) -> list[str]:
        return ["Basico::Bandera"]
