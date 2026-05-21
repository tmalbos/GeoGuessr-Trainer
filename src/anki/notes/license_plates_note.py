import yaml

from .base import Note


class LicensePlatesNote(Note):
    def __init__(self, country_data: dict):
        Note.__init__(self, country_data)
        self.MODEL = "GeoGuessr License Plates"
        self.license_plates = country_data.get("license_plates")

    def _normalize_license_plate(self, plate: dict) -> dict:
        def build_side(prefix: str) -> dict | None:
            side = {}

            if prefix == "front":
                side["is_required"] = plate.get("front_is_required", True)

            color = plate.get(f"{prefix}_color")
            if color is not None:
                side["color"] = color

            letter_color = plate.get(f"{prefix}_letter_color")
            if letter_color is not None:
                side["letter_color"] = letter_color

            shape = plate.get(f"{prefix}_shape")
            if shape is not None:
                side["shape"] = shape

            strips = []

            for i in (1, 2):
                strip_color = plate.get(f"{prefix}_strip_color_{i}")
                strip_side = plate.get(f"{prefix}_strip_side_{i}")

                if strip_color is None and strip_side is None:
                    continue

                strip = {}

                if strip_color is not None:
                    strip["color"] = strip_color

                if strip_side is not None:
                    strip["side"] = strip_side

                strips.append(strip)

            if strips:
                side["strips"] = strips

            return side or None

        normalized = {
            "car_type": plate["car_type"],
            "front": build_side("front"),
        }

        back = build_side("back")

        if back and back != normalized["front"]:
            normalized["back"] = back

        return normalized

    def model(self) -> str:
        return self.MODEL

    def fields(self) -> dict | None:
        if not self.license_plates:
            return None

        normalized = [self._normalize_license_plate(plate) for plate in self.license_plates]

        raw = yaml.dump({"license_plates": normalized}, allow_unicode=True, sort_keys=False).strip()

        return {
            "Pregunta": f"¿Cómo son las matrículas de {self.country_name}?",
            "YamlData": f"<pre>{raw}</pre>",
        }

    def tags(self) -> list[str]:
        return ["Basico::Matriculas"]
