# GeoGuessr Trainer

A local web app that imports your GeoGuessr games into PostgreSQL, enriches every round with geographic data, and shows you where you are strong and where you lose points. It also includes a clue-exploration map and a script-reading minigame.

Everything runs on your machine: a FastAPI backend, a React frontend, and a Postgres database.

## Features

### Sync
- **Split "Sync games" button** on the History page. The caret lets you choose which match types to import: Daily challenges, Challenges, Duels.
- Imports from your private GeoGuessr feed (Standard game mode only) and your duel history. Games already in the database are skipped.
- **Live progress** over Server-Sent Events. The event log is kept server-side, so refreshing the page re-attaches to a running sync. Only one sync can run at a time.
- **Cookie handling**: if the `_ncfa` cookie expires mid-sync, the app tries to refresh it from your browser (Brave, Chrome or Edge on Windows) and retries once.
- For each round it:
  - enriches real and guessed coordinates (country, state, subregion, city, biome, urban/rural),
  - downloads and compresses the round replay (movement, panning, zooming, map events),
  - derives steps and time from the replay for duels, and infers the move type (`moving`, `no_move`, `nmpz`),
  - saves the game, rounds and replays in one transaction.

### History
- Paginated list of every game with the first 5 rounds inline (flag, score, time, steps) and totals.
- Filters: game type, game mode, time limit, min/max total score.
- Sort by date, mode, total score, total steps or total time.
- Expand a game to see every round: actual location vs. your guess, area type, time, steps, score.

### Analysis
- Pick a combination of game type, move type and time limit (defaults: Daily / Moving / 3 min).
- Levels: **General, Continent, Biome, Country**. A level appears only if some zone has at least 10 rounds.
- Per zone:
  - **Current level**: median distance over the last 100 rounds, converted to a score and labelled (Terrible → Decent → High → Exceptional → Elite → Inhuman), with a bootstrap 95% CI once there are 20+ rounds.
  - **Worst rounds**: 90th-percentile distance.
  - **Consistency**: standard deviation of distance.
  - **Trend arrows** comparing the last 10 rounds to the 20 before them (a change under 5% shows →).
  - **Sparkline** of distance per round (last 100).
  - **Confusions**: the real→guessed zone mix-ups that cost the most km in the last 30 rounds, plus sub-level confusions (continent→country, country→state).
- Zones are sorted weakest first.

### Explore
- Zoomable, pannable world map (mouse wheel, drag, pinch, keyboard +/−/arrows). Tiny countries get click dots. The view is remembered when you come back.
- Click a country to open its page, which shows a map of its first-level subdivisions.
- The clue tabs on the country page are a plug-in point (`pages/Explore/sections.js`). No sections are registered yet, so countries show "Coming soon".

### Study: Scripts trainer
- Practice reading real place names written in **Greek, Russian (Cyrillic), Georgian and Arabic**, and type their transliteration.
- Names come from GeoNames dumps (GR, CY, BG, RU, GE, AE, OM, KW, TN, JO).
- Optional time limit (1–15 min). Timed sessions are saved to `data/study/scripts/sessions.json` and shown as history per script. Free practice is not saved.
- Reference table for each alphabet (Arabic also has a positional-forms table).
- To add a new script, create a module in `frontend/src/pages/Study/ScriptsTrainer/languages/` and register it in `index.js`. To add a new minigame, add a folder and one entry in `Study/games.js`.

### Settings
- System status: database, Anki, geo layers, cookie.
- Save the GeoGuessr cookie, or refresh it from your browser.
- GeoGuessr user ID and interface language (English / Español).

### Anki (partial)
- `AnkiConnectClient` is created at startup and the status grid reports whether AnkiConnect (`127.0.0.1:8765`) is reachable.
- `GeoSignalRepository` can fetch road lines and license plates per country to feed cards.
- Card generation is **not currently active** in the sync pipeline: the Anki check in `src/api/routers/sync.py` is commented out and `process_game` returns no Anki errors.

## Architecture

```
React (Vite)  ──/api──▶  FastAPI  ──▶  PostgreSQL (asyncpg)
                            │
                            ├──▶ GeoGuessr API (httpx, _ncfa cookie)
                            ├──▶ LocalGeo (GeoParquet: biomes, urban areas, admin levels)
                            └──▶ Nominatim (fallback, 1 request/second)
```

Geo enrichment tries the local GeoParquet layers first and only calls Nominatim when a point isn't covered by a local admin file. Region overrides (`src/geo/region_overrides.py`) map territories that Nominatim reports under a parent country (Hong Kong, Puerto Rico, Greenland, …) to their own country code. Geo layers load in a background task at startup, so the API is available while they load (the status page shows "Loading geo layers…").

## Requirements

- Python 3.10+
- PostgreSQL
- Node.js 18+
- Optional: Anki with the AnkiConnect add-on; Playwright (browser-cookie fallback)

Python packages used: `fastapi`, `uvicorn`, `asyncpg`, `httpx`, `pydantic`, `python-dotenv`, `pyyaml`, `aiofiles`, `geopandas`, `shapely`, `numpy`, `pyarrow` (for GeoParquet), and optionally `playwright`.

## Setup

### 1. Database

```bash
createdb geoguessr_trainer
psql geoguessr_trainer -f src/db/schema.sql
```

The `country` and `biome` tables must be populated before the first sync: rounds reference them by foreign key, and every real location needs a biome.

### 2. Environment

Create a `.env` file in the project root:

```env
PG_DSN=postgresql://user:password@localhost:5432/geoguessr_trainer
GEOGUESSR_USER_ID=<your GeoGuessr user id>
GEOGUESSR_LANG=en        # en | es
```

If `GEOGUESSR_USER_ID` is not set, a hardcoded default in `src/sync/user_identity.py` is used. Set your own, otherwise duel rounds and daily games won't match your account.

### 3. Geo data

Local layers live in `data/geo/` and are built with `scripts/convert_geo.py`:

```
data/geo/
  biomes.parquet       BIOME_NAME, geometry
  urban.parquet        geometry
  countries.parquet    code, name, geometry      (optional)
  admin/{CC}.parquet   state, [subregion], city, geometry
```

`biomes.parquet` and `urban.parquet` are required. If they are missing the sync stops with "Geo layers failed to load".

For the Explore map, run `python scripts/build_world_map.py`, which generates `world.json` and `admin1/{CC}.json` served from `/geo`.

For the Scripts trainer, place GeoNames country dumps in `data/study/scripts/geonames/` as `{CC}.txt` (e.g. `GR.txt`, `RU.txt`).

### 4. Run

Backend:

```bash
uvicorn src.api.app:app --port 8000
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

The frontend calls `/api/*`, so the Vite dev server must proxy `/api` to `http://localhost:8000` (see `vite.config.js`). `npm run build` writes to `frontend/dist`.

### 5. First sync

1. Open **Settings** and paste your `_ncfa` cookie (or use **Refresh from browser**).
2. Check that **System status** shows the database, geo layers and cookie as OK.
3. Go to **History** and press **Sync games**.

## API

All routes are under `/api`.

| Method | Route | Purpose |
|---|---|---|
| GET | `/status` | DB, Anki, geodata state, cookie, sync running |
| POST | `/sync` | Start a sync (`{"match_types": [...]}`); 409 if one is running |
| GET | `/sync/events` | SSE stream of sync events (replayed from the start) |
| GET | `/history` | Games with filters, sorting and pagination |
| GET | `/analysis/filters` | Available match type / move type / time limit options |
| GET | `/analysis/levels` | Geo levels with enough rounds for a filter combo |
| GET | `/analysis/{level}` | Per-zone stats and confusions for a level |
| GET / PUT | `/settings`, `/settings/cookie`, `/settings/lang`, `/settings/user` | Read and change settings |
| POST | `/settings/cookie/refresh` | Re-extract the cookie from the browser |
| GET | `/study/scripts/geonames/{CC}` | GeoNames dump for the trainer |
| GET / POST | `/study/scripts/sessions` | Timed-session history |

## Project layout

```
src/
  api/              FastAPI app, dependencies and routers
  analysis/         scoring, zone stats, confusions, level discovery
  db/               schema.sql, pool, repositories (games, cities, geo signals)
  geo/              LocalGeo, enrichment service, region overrides
  geoguessr/        API client, cookie store/extraction, replay, normalization
  sync/             orchestrator, challenge/duel pipelines, game ingest
  i18n/             translations loaded from locales/{lang}.yaml
  anki/             AnkiConnect client
  app_context.py    owns the DB pool, HTTP client and geo layers
frontend/src/
  pages/            Explore, History, Analysis, Study, Settings
  components/       SyncButton, StatusGrid, Flag
  lib/              api helper, formatting
data/
  geo/              GeoParquet layers
  study/scripts/    GeoNames dumps and sessions.json
```

## Things to know

- The cookie is stored in plain text in `geoguessr_cookie.txt` in the working directory. Keep it out of version control.
- Cookie extraction from the browser is written for Windows paths (Brave, Chrome, Edge). Chrome often encrypts cookie values, in which case the Playwright fallback is used. Otherwise paste the cookie manually.
- Changing the language or user ID in Settings updates the running process only. To make them permanent, edit `.env`.
- Nominatim fallbacks are rate-limited to one request per second, so a first sync of many games in uncovered regions can be slow.
- The Scripts trainer's alphabet descriptions are in Spanish, while the rest of the UI is in English.