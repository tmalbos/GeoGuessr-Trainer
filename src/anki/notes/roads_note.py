import yaml

from .base import Note


class RoadsNote(Note):
    def __init__(self, country_data: dict):
        Note.__init__(self, country_data)
        self.MODEL = "GeoGuessr Roads"
        self.roads = country_data.get("roads")

    def _normalize_road_line(self, line: dict) -> dict:
        def build_section(name: str) -> dict | None:
            section = {}

            color = line.get(f"{name}_color")
            if color is not None:
                section["color"] = color

            count = line.get(f"{name}_count")
            if count is not None:
                section["count"] = count

            pattern = line.get(f"{name}_pattern")
            if pattern is not None:
                section["pattern"] = pattern

            return section or None

        normalized = {
            "rule": line["rule"].replace("_", "-"),
        }

        inner = build_section("inner")
        if inner:
            normalized["inner"] = inner

        outer = build_section("outer")
        if outer:
            normalized["outer"] = outer

        extra = build_section("extra")
        if extra:
            normalized["extra"] = extra

        return normalized

    def model(self) -> str:
        return self.MODEL

    def fields(self) -> dict | None:
        if not self.roads:
            return None

        normalized = [self._normalize_road_line(line) for line in self.roads]

        raw = yaml.dump(
            {"roads": {"lines": normalized}}, allow_unicode=True, sort_keys=False
        ).strip()

        return {
            "Pregunta": f"¿Cómo son las líneas de las rutas de {self.country_name}?",
            "YamlData": f"<pre>{raw}</pre>",
        }

    def tags(self) -> list[str]:
        return ["Basico::Rutas::Lineas"]
