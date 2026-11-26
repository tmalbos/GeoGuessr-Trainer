"""national.py — The national Anki card: "From which country is this X?" or its inverse."""


def national_card(
    cc: str, country: str, what: str, tags: list[str], clue: dict, invert: bool
) -> dict:
    """The national card: name the country from the clue, or the clue from the country."""
    if invert:
        return {
            "tags": tags,
            "cc": cc,
            "question": f"How is the {what} from {country}?",
            "answer": clue["info"],
            "image": None,
        }
    return {
        "tags": tags,
        "cc": cc,
        "question": f"From which country is this {what}?",
        "answer": country,
        "image": clue.get("image"),
    }
