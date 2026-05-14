import yaml

from .base import Note


class LicensePlatesNote(Note):
    def __init__(self, country_data: dict):
        Note.__init__(self, country_data)
        self.MODEL = "GeoGuessr License Plates"
        self.license_plates = country_data.get("license_plates")

    def model(self) -> str:
        return self.MODEL

    def fields(self) -> dict | None:
        if not self.license_plates:
            return None

        raw = yaml.dump(
            {"license_plates": self.license_plates}, allow_unicode=True, sort_keys=False
        ).strip()

        return {
            "Pregunta": f"¿Cómo son las matrículas de {self.country_name}?",
            "YamlData": f"<pre>{raw}</pre>",
        }

    def tags(self) -> list[str]:
        return ["Basico::Matriculas"]
