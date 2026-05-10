# GeoGuessr Trainer

A command-line tool to analyze GeoGuessr games, enrich round data with geographic context (ecoregions, traffic signs, license plates), and generate Anki flashcards to improve your gameplay.

## What it does

- Downloads game results from the GeoGuessr API using your session cookie
- Stores rounds in MongoDB with enriched metadata (ecoregion, biome, stop sign shape and text, front plate requirement)
- Displays performance analysis grouped by geographic granularity (continent, country, ecoregion, etc.)
- Exports Anki cards for the countries where you struggle most

## Requirements

- Python 3.11+
- MongoDB running locally (or on another host, configurable via environment variable)
- An active GeoGuessr account (to obtain the session cookie)

## Installation

```bash
git clone https://github.com/tmalbos/GeoGuessr-Trainer.git
cd GeoGuessr-Trainer

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -e .
playwright install chromium       # required for automatic cookie renewal
```

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

`.env`:
```
MONGO_URI=mongodb://localhost:27017/
```

## Populating the database

Before using the analyzer, you need to load the base geographic data:

```bash
# Basic data: traffic signs, license plates, stop sign shapes (no extra download needed)
python -m populate

# With terrain data (ecoregions per state/province)
# 1. Download the shapefile from https://ecoregions.appspot.com
# 2. Unzip it to a local folder
python -m populate --terrain /path/to/Ecoregions2017.shp
```

The terrain step can take several minutes on the first run, as it computes geographic intersections for ~250 countries.

## Usage

```bash
python main.py
```

The main menu has three options:

| Option | Description |
|--------|-------------|
| `[1] Insert game` | Paste a GeoGuessr game link or ID |
| `[2] Data analysis` | Performance stats by accumulated data level |
| `[3] Update cookie` | Refreshes the session cookie if it expired |

To get your game link: after finishing a game on GeoGuessr, copy the results URL — it looks like `geoguessr.com/results/XXXXXXXXXXX`.

## Development

```bash
pip install -e ".[dev]"

ruff check .          # linter
ruff format .         # formatter
mypy core/ db/ anki/  # type checker
pytest                # tests
```

## Project structure

```
GeoGuessr-Trainer/
├── main.py              # Entrypoint — TUI menu
├── populate.py          # Database setup script
├── core/
│   ├── api.py           # GeoGuessr API client
│   ├── auth.py          # Session cookie management
│   ├── analyzer.py      # Round parsing and display
│   ├── eco_enrich.py    # Ecoregion enrichment (background thread)
│   └── stats.py         # Performance analysis
├── db/
│   ├── mongo.py         # MongoDB connection and helpers
│   └── schema.yaml      # Document validation schema
├── anki/                # Flashcard generation
└── tests/
    ├── conftest.py
    ├── unit/            # One file per function
    └── integration/
```

## GeoGuessr session cookie

GeoGuessr uses a `_ncfa` cookie to authenticate requests to its private API. The tool tries to extract it automatically using Playwright (Brave, Chrome, and Edge are supported). If that fails, it falls back to manual input.

To get it manually: open DevTools in your browser → Application → Cookies → `geoguessr.com` → copy the value of `_ncfa`.

The cookie is stored locally in `geoguessr_cookie.txt` (git-ignored). Option `[3]` in the menu triggers an automatic renewal attempt before asking for manual input.

## License

MIT