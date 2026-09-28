"""pool.py — asyncpg connection pool lifecycle."""

import asyncpg
from asyncpg import Pool


async def init_pool(dsn: str = "") -> Pool:
    """Create and return a connection pool. Call once at startup."""
    return await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)


async def close_pool(pool: Pool | None) -> None:
    """Close the given pool. Call at shutdown."""
    if pool:
        await pool.close()
