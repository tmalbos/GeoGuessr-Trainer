import yaml

from src.db.geo_signals import ROAD_LINE_SPEC, normalize_geo_signals
from src.i18n.lang import translate

from .base import Note


class RoadsNote(Note):
    def __init__(self, country_data: dict, **kwargs):
        Note.__init__(self, country_data, **kwargs)
        self.MODEL = "GeoGuessr Roads"
        self.roads = country_data.get("roads")

    def model(self) -> str:
        return self.MODEL

    def fields(self) -> dict | None:
        if not self.roads:
            return None

        normalized = [normalize_geo_signals(line, ROAD_LINE_SPEC) for line in self.roads]

        raw = yaml.dump(
            {"roads": {"lines": normalized}}, allow_unicode=True, sort_keys=False
        ).strip()

        return {
            "Pregunta": translate(
                "What do the road lines of {country} look like?", country=self.country_name
            ),
            "YamlData": f"<pre>{raw}</pre>",
        }

    def tags(self) -> list[str]:
        return ["Basico::Rutas::Lineas"]
