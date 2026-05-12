import re
import sys
import unicodedata
from pathlib import Path

import yaml
from pymongo import MongoClient

# === CONFIG ===
MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "geoguessr"
COLLECTION_NAME = "geo_signals"
DATA_DIR = Path("./src/db/data")


def _remove_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _pascal(name: str) -> str:
    cleaned = _remove_accents(name.strip())
    return "".join(
        word.capitalize()
        for word in re.split(r"[\s\-_]+", cleaned)
        if word.lower() not in ("y", "and")
    )


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _remove_accents(name).lower())


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def update_country(collection, country_code: str):
    doc = collection.find_one({"country_code": country_code}, {"country_name": 1, "_id": 0})

    if not doc:
        print(f"[ERROR] No country found for code: {country_code}")
        return

    country_name = doc["country_name"]
    file_name = f"{_pascal(country_name)}.yaml"
    yaml_path = DATA_DIR / file_name

    if not yaml_path.exists():
        print(f"[ERROR] YAML not found for {country_code}: {yaml_path}")
        return

    update_fields = load_yaml(yaml_path)

    result = collection.update_one({"country_code": country_code}, {"$set": update_fields})

    print(
        f"[{country_code}] {country_name} -> "
        f"Matched: {result.matched_count}, Modified: {result.modified_count}"
    )


def main():
    client = MongoClient(MONGO_URI)
    collection = client[DB_NAME][COLLECTION_NAME]

    # If country codes were provided
    if len(sys.argv) > 1:
        country_codes = [code.upper().lstrip(".") for code in sys.argv[1:]]

        for country_code in country_codes:
            update_country(collection, country_code)

        return

    countries = list(
        collection.find(
            {"country_name": {"$exists": True}}, {"country_code": 1, "country_name": 1, "_id": 0}
        )
    )

    country_lookup = {_normalize_name(doc["country_name"]): doc for doc in countries}

    for yaml_path in sorted(DATA_DIR.glob("*.yaml")):
        try:
            normalized_yaml_name = _normalize_name(yaml_path.stem)

            doc = country_lookup.get(normalized_yaml_name)

            if not doc:
                print(f"[ERROR] No country found for YAML: {yaml_path.name}")
                continue

            update_fields = load_yaml(yaml_path)

            result = collection.update_one(
                {"country_code": doc["country_code"]}, {"$set": update_fields}
            )

            print(
                f"[{doc['country_code']}] {doc['country_name']} -> "
                f"Matched: {result.matched_count}, Modified: {result.modified_count}"
            )

        except Exception as e:
            print(f"[ERROR] Failed processing {yaml_path.name}: {e}")


if __name__ == "__main__":
    main()
