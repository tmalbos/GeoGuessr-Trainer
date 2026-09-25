"""events.py — Structured events emitted by the pipeline (replaces print())."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    type: str  # log | feed | game_started | round_result | game_done | anki_errors | error | done
    data: dict[str, Any] = field(default_factory=dict)


Emit = Callable[[Event], Awaitable[None]]


async def noop(_: Event) -> None:
    """Default emitter: drop everything."""
