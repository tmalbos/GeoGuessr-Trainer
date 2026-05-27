import yaml

from src.db.geo_signals import LICENSE_PLATE_SPEC, normalize_geo_signals
from src.i18n.lang import translate

from .base import Note


class LicensePlatesNote(Note):
    def __init__(self, country_data: dict, **kwargs):
        Note.__init__(self, country_data, **kwargs)
        self.MODEL = "GeoGuessr License Plates"
        self.license_plates = country_data.get("license_plates")

    def model(self) -> str:
        return self.MODEL

    def fields(self) -> dict | None:
        if not self.license_plates:
            return None

        normalized = [
            normalize_geo_signals(plate, LICENSE_PLATE_SPEC) for plate in self.license_plates
        ]

        raw = yaml.dump({"license_plates": normalized}, allow_unicode=True, sort_keys=False).strip()

        return {
            "Pregunta": translate(
                "What are the license plates of {country} like?", country=self.country_name
            ),
            "YamlData": f"<pre>{raw}</pre>",
        }

    def tags(self) -> list[str]:
        return ["Basico::Matriculas"]
