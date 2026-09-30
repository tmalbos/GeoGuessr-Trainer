"""app_context.py — Owns all long-lived resources (pool, http clients, geo layers, cookie)."""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from src.anki.anki_connect import AnkiConnectClient
from src.db.pool import close_pool, init_pool
from src.db.repositories.game_repository import GameRepository
from src.db.repositories.geo_signal_repository import GeoSignalRepository
from src.geo.enrichment_service import GeoEnrichClient
from src.geo.local_geo import LocalGeo
from src.geoguessr.cookie_store import load_cookie

if TYPE_CHECKING:
    import asyncpg

_GEO_DIR = Path(__file__).parents[1] / "data" / "geo"


class AppContext:
    """Owns all long-lived resources. Single init/cleanup lifecycle.

    Construction is synchronous and cheap.  Call ``init()`` to start
    heavyweight resources (DB pool and geo layer loading).
    """

    def __init__(self, db_dsn: str) -> None:
        self.db_dsn = db_dsn

        self.db_pool: asyncpg.Pool | None = None
        self._db_adapter: GameRepository | None = None
        self.geo_signal_repo: GeoSignalRepository | None = None

        # Local geo layers (biomes, urban areas, admin)
        self.local_geo: LocalGeo | None = None
        self._geodata_task: asyncio.Task | None = None
        self.geodata_ready = asyncio.Event()

        # GeoGuessr
        self.http_client = None
        self.geoguessr_ready = asyncio.Event()

        # Geographical Enrich
        self.geo_client = None
        self.geo_ready = asyncio.Event()

        # Anki
        self.anki_client = None
        self.anki_ready = asyncio.Event()

    @property
    def db_adapter(self) -> GameRepository:
        """Return the GameRepository instance. Raises RuntimeError if not initialized."""
        if self._db_adapter is None:
            msg = "AppContext not initialized — call await init() first"
            raise RuntimeError(msg)
        return self._db_adapter

    async def init(self) -> None:
        """Create DB pool (blocking). Kick off background geo layer loading."""
        self.db_pool = await init_pool(dsn=self.db_dsn)
        self._db_adapter = GameRepository(self.db_pool)
        self.geo_signal_repo = GeoSignalRepository(self.db_pool)
        self._geodata_task = asyncio.create_task(self._startup())

    async def _startup(self) -> None:
        self.http_client = httpx.AsyncClient(headers={"User-Agent": "GeoGuessr-Analyzer/1.0"})
        self.geoguessr_ready.set()

        self.anki_client = AnkiConnectClient(self.http_client)
        self.anki_ready.set()

        self.geo_client = GeoEnrichClient(self.http_client)
        self.geo_ready.set()

        await self._load_geodata()

    async def _load_geodata(self) -> None:
        """Load biome/urban/country-index GeoParquet files (background, via executor)."""
        try:
            loop = asyncio.get_running_loop()
            local = LocalGeo(_GEO_DIR)
            await loop.run_in_executor(None, local.load)
            self.local_geo = local
            self.geo_client.local_geo = local
        finally:
            self.geodata_ready.set()

    async def aclose(self) -> None:
        """Release all resources."""
        if self._geodata_task is not None and not self._geodata_task.done():
            self._geodata_task.cancel()
        await close_pool(self.db_pool)
        self.db_pool = None
        self._db_adapter = None
        self.geo_signal_repo = None
        if self.http_client is not None:
            await self.http_client.aclose()
        self.local_geo = None

    def create_geoguessr_client(self):
        """Return a GeoguessrClient wired to the owned cookie and shared http client."""
        from src.geoguessr.client import GeoguessrClient

        ncfa_cookie = load_cookie()

        return GeoguessrClient(ncfa_cookie, http_client=self.http_client)
