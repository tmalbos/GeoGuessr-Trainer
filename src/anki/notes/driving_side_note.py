from .base import Note


class DrivingSideNote(Note):
    def __init__(self, country_data: dict):
        Note.__init__(self, country_data)
        self.SEPARATOR = "<br>"
        self.MODEL = "Single Choice"
        self.side = country_data.get("car", {})["side"].lower()

    def model(self) -> str:
        return self.MODEL

    def fields(self) -> dict | None:
        return {
            "Pregunta": f"¿De qué lado se maneja en {self.country_name}?",
            "Opciones": f"Izquierda{self.SEPARATOR}Derecha",
            "Respuesta": "1" if self.side == "left" else "2",
            "Mezclar": "false",
        }

    def tags(self) -> list[str]:
        return ["Basico::LadoConduccion"]
