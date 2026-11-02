# GeoGuessr Trainer

Your GeoGuessr games, imported and read back to you. It is a small web app that runs on your own machine — a React frontend, a FastAPI backend and a PostgreSQL database — so your games, replays and stats stay in one place you control.

![Replay report](docs/images/replay-report.webp)

## Replay report

Every game can be rebuilt from its replay: a timeline of what you did, where you moved, what you looked at and when you searched the map. Each moment is graded, and the round is split into looking for clues, searching the map, pinpointing and verifying.

The report turns that into something useful. It shows what the round cost ("1m 57s and 9 steps could have been saved") and opens with **What to work on**: the two mistakes that lost the most points, each linked to the clues for the right country, region and setting.

![Round detail](docs/images/replay-round.webp)

Click an event to see the map and the street-view frame behind it. The route on the map shows the real location, your guess, and how far you moved away from it.

## Analysis

Your zones ranked weakest first, at the level you choose: general, continent, biome or country. Each zone shows its current level (from the median distance of your last 100 rounds), your worst 10% and consistency, whether you are improving, a sparkline, and the mix-ups that cost you most — down to state level.

Filters follow the game you care about: daily / challenge / duel, moving / no move, time limit. Zones with fewer than 10 rounds are left out, so the picture stays honest.

![Analysis](docs/images/analysis.webp)

## Explore

A world map you can zoom into, down to the smallest countries. Click a country to open its clue sheet: bollards, road lines, electricity poles, plates, architecture — photos with short notes, filterable by category, urban or rural, national or regional.

The side panel carries your stats for that country and a map of its subdivisions. You can add or edit clues, or pull in a Plonkit guide for a country you are learning.

![Country clues](docs/images/explore-country.webp)

## History and sync

Every game in one list, with its first five rounds inline — flag, score, time, steps — and the totals. Filter by game type, mode, time limit or score, sort by date or performance, and open any game in the replay report.

**Sync games** imports daily challenges, challenges and duels. You pick the types, progress streams live, and games you already have are skipped. Each round is stored with its location enriched — country, state, city, biome, urban or rural — for both the real place and your guess.

![History](docs/images/history.webp)

## Scripts trainer

Practice reading the alphabets that show up in the game: Greek, Russian (Cyrillic), Georgian and Arabic. Real place names appear in their own script and you type the transliteration. A wrong answer shows exactly which characters you missed.

Sessions can be free practice or timed (1–15 minutes). Timed sessions are saved with your accuracy history, and every alphabet has a reference table.

![Scripts trainer](docs/images/scripts-trainer.webp)

## Running it

You need Python 3.10+, PostgreSQL, Node.js 18+ and a GeoGuessr account.

```bash
# 1. Database
createdb geoguessr_trainer
psql geoguessr_trainer -f src/db/schema.sql
python populate.py --terrain /path/to/Ecoregions2017.shp

# 2. Environment (.env in the project root)
#    PG_DSN=postgresql://user:password@localhost:5432/geoguessr_trainer
#    GEOGUESSR_USER_ID=<your user id>
#    GEOGUESSR_LANG=en        # en | es

# 3. Geo layers and map data
python -m scripts.convert_geo biomes Ecoregions2017/Ecoregions2017.shp
python -m scripts.convert_geo urban  ne_10m_urban_areas/ne_10m_urban_areas.shp
python scripts/build_world_map.py

# 4. Run
uvicorn src.api.app:app --port 8000
cd frontend && npm install && npm run dev
```

Open **Settings**, paste your GeoGuessr `_ncfa` cookie (or refresh it from your browser), check that the status grid is green, then press **Sync games** on **History**.

A few practical notes:

- The cookie is stored in plain text in `geoguessr_cookie.txt`, which is gitignored.
- Locations are enriched from local GeoParquet layers. Anything not covered falls back to Nominatim at one request per second, so a first sync of many games can take a while.
- The scripts trainer reads GeoNames country dumps from `data/study/scripts/geonames/{CC}.txt`.
- Changing the language or user id in Settings affects the running process only; set them in `.env` to keep them.
