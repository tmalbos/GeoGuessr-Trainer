"""
app_context.py — Owns all long-lived resources (pool, http clients, shapefile, cookie).
Design checkpoint: method bodies are stubs.
"""

import asyncio

import asyncpg
import httpx


class GeoEnrichClient:
    """Wraps geo-enrichment API calls. Stub — full impl later."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client
        self._semaphore = asyncio.Semaphore(1)


class AnkiConnectClient:
    """Wraps AnkiConnect API calls. Stub — full impl later."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client


class AppContext:
    """Owns all long-lived resources. Single init/cleanup lifecycle.

    Construction is synchronous and cheap.  Call ``init()`` to start
    heavyweight resources (DB pool) — everything non-DB loads in the
    background so startup is not blocked.
    """

    def __init__(self, db_dsn: str, ncfa_cookie: str) -> None:
        self.db_dsn = db_dsn
        self.cookie = ncfa_cookie

        # ── Resources (set in __init__ or init) ──────────────────────
        self.db_pool: asyncpg.Pool | None = None
        self.ecoregion_gdf = None  # gpd.GeoDataFrame — loaded in background

        # Cheap resources created synchronously
        self.http_client = httpx.AsyncClient()
        self.geo_client = GeoEnrichClient(self.http_client)
        self.anki_client = AnkiConnectClient(self.http_client)

    async def init(self) -> None:
        """Create DB pool (awaited). Kick off background loading for everything else."""
        self.db_pool = await asyncpg.create_pool(dsn=self.db_dsn)
        _ = asyncio.create_task(self._load_background_resources())

    async def _load_background_resources(self) -> None:
        """Load ecoregions, warm caches, etc. — non-blocking."""
        # Stub: will load ecoregion_gdf from shapefile in background
        pass

    async def aclose(self) -> None:
        """Release all resources."""
        if self.db_pool is not None:
            await self.db_pool.close()
            self.db_pool = None
        await self.http_client.aclose()
        self.ecoregion_gdf = None

    def create_geoguessr_client(self):
        """Return a GeoguessrClient wired to the owned cookie."""
        from src.core.api import GeoguessrClient

        return GeoguessrClient(self.cookie)
