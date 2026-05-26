"""
app_context.py — Owns all long-lived resources (pool, http clients, shapefile, cookie).
"""

import asyncio
from pathlib import Path

import asyncpg
import geopandas as gpd
import httpx

from src.anki.anki_connect import AnkiConnectClient
from src.core.auth import load_cookie
from src.core.geo_enrich import GeoEnrichClient
from src.db.db import DbAdapter, close_pool, init_pool

_DEFAULT_SHP = Path("Ecoregions2017/Ecoregions2017.shp")


class AppContext:
    """Owns all long-lived resources. Single init/cleanup lifecycle.

    Construction is synchronous and cheap.  Call ``init()`` to start
    heavyweight resources (DB pool and shapefile loading).
    """

    def __init__(self, db_dsn: str) -> None:
        self.db_dsn = db_dsn
        self._shp_path = None

        # ── Resources (set in __init__ or init) ──────────────────────
        self.db_pool: asyncpg.Pool | None = None
        self._db_adapter: DbAdapter | None = None
        self.ecoregion_gdf = None

        # Ecoregion
        self._ecoregion_task: asyncio.Task | None = None
        self.ecoregion_ready = asyncio.Event()

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
    def db_adapter(self) -> DbAdapter:
        """Return the DbAdapter instance. Raises RuntimeError if not initialized."""
        if self._db_adapter is None:
            raise RuntimeError("AppContext not initialized — call await init() first")
        return self._db_adapter

    async def init(self) -> None:
        """Create DB pool (blocking). Kick off background shapefile loading."""
        self.db_pool = await init_pool(dsn=self.db_dsn)
        self._db_adapter = DbAdapter(self.db_pool)
        self._ecoregion_task = asyncio.create_task(self._startup())

    async def _startup(self) -> None:
        self.http_client = httpx.AsyncClient(headers={"User-Agent": "GeoGuessr-Analyzer/1.0"})
        self.geoguessr_ready.set()

        self.anki_client = AnkiConnectClient(self.http_client)
        self.anki_ready.set()

        self.geo_client = GeoEnrichClient(self.http_client)
        self.geo_ready.set()

        await self._load_ecoregions()

    async def _load_ecoregions(self) -> None:
        """Load ecoregion GeoDataFrame from shapefile (background, via executor)."""
        self._shp_path = Path(_DEFAULT_SHP) if _DEFAULT_SHP else None

        if self._shp_path is not None and not self._shp_path.exists():
            raise FileNotFoundError(
                f"Shapefile not found: {self._shp_path.resolve()}\n"
                "Download from https://ecoregions.appspot.com/ and place it at the expected path."
            )

        try:
            loop = asyncio.get_running_loop()
            gdf = await loop.run_in_executor(
                None,
                lambda: gpd.read_file(self._shp_path)[
                    ["ECO_NAME", "BIOME_NAME", "REALM", "geometry"]
                ].to_crs(epsg=4326),
            )
            self.ecoregion_gdf = gdf
            self.geo_client.ecoregion_gdf = gdf
        finally:
            self.ecoregion_ready.set()

    async def aclose(self) -> None:
        """Release all resources."""
        if self._ecoregion_task is not None and not self._ecoregion_task.done():
            self._ecoregion_task.cancel()
        await close_pool(self.db_pool)
        self.db_pool = None
        self._db_adapter = None
        if self.http_client is not None:
            await self.http_client.aclose()
        self.ecoregion_gdf = None

    def create_geoguessr_client(self):
        """Return a GeoguessrClient wired to the owned cookie and shared http client."""
        from src.core.api import GeoguessrClient

        ncfa_cookie = load_cookie()

        return GeoguessrClient(ncfa_cookie, http_client=self.http_client)
