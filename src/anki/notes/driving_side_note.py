from src.i18n.lang import translate

from .base import Note


class DrivingSideNote(Note):
    def __init__(self, country_data: dict, **kwargs) -> None:
        Note.__init__(self, country_data, **kwargs)
        self.SEPARATOR = "<br>"
        self.MODEL = "Single Choice"
        self.side = country_data.get("car", {})["side"].lower()

    def model(self) -> str:
        return self.MODEL

    def fields(self) -> dict | None:
        return {
            "Pregunta": translate(
                "Which side of the road does {country} drive on?",
                country=self.country_name,
            ),
            "Opciones": f"{translate('Left')}{self.SEPARATOR}{translate('Right')}",
            "Respuesta": "1" if self.side == "left" else "2",
            "Mezclar": "false",
        }

    def tags(self) -> list[str]:
        return ["Basico::LadoConduccion"]
