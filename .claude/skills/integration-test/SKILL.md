---
name: integration-test
description: >
  Writes hermetic, behavior-driven pytest integration tests for GeoGuessr Trainer
  that exercise real infrastructure (PostgreSQL, real GeoGuessr API, real Nominatim,
  Playwright browser) across a trust boundary. Tests document observable cross-service
  outcomes, not implementation.
  Trigger: when the user asks for an integration test, asks to test a workflow / endpoint
  end-to-end against real services, or asks to verify a real seam (DB, external API).
metadata:
  author: tmalbos
  version: "1.0"
---

## When to Use

- Verifying a flow that touches **real infrastructure** (real PostgreSQL, real GeoGuessr API, real Nominatim — not stubs)
- Testing the full sync pipeline end-to-end: GeoGuessr API → enrichment → PostgreSQL persistence
- Testing Nominatim reverse-geocoding against the real API
- Testing AnkiConnect integration against a running Anki instance
- Testing Playwright cookie extraction against a real browser
- Verifying DB migrations, indexes, or data-layer behavior that a unit test cannot observe

**Do not** use this skill for:
- Pure function logic with no I/O — that is a unit test ([[tmalbos-geoguessr-unit-test]])
- UI / end-to-end browser flows — those belong to a separate suite

The rule of thumb: **if you could meaningfully test it by stubbing the dependency, it is a unit test**. Integration tests exist precisely because stubbing would erase the bug.

---

## Critical Patterns

### File location — MANDATORY

Tests live under `tests/integration/<module-path>/<function_or_flow_name>.py` — same layout as unit tests, only the second segment changes from `unit` to `integration`.

| What is tested | Test file |
|----------------|-----------|
| `src/core/sync.py` → full game sync pipeline | `tests/integration/core/sync/full_game_sync.py` |
| `src/core/geo_enrich.py` → `reverse_geocode` against real Nominatim | `tests/integration/core/geo_enrich/reverse_geocode.py` |
| `src/core/api.py` → `fetch_game_results` against real GeoGuessr API | `tests/integration/core/api/fetch_game_results.py` |
| `src/db/db.py` → `save_game` into real PostgreSQL | `tests/integration/db/db/save_game.py` |
| `src/db/update_country.py` → sync YAML data into real PostgreSQL | `tests/integration/db/update_country/sync_country_data.py` |
| `src/anki/anki_connect.py` → card creation against real Anki | `tests/integration/anki/anki_connect/create_cards.py` |

One file per tested flow.

### Integration marker — MANDATORY

Every integration test file declares the marker at the top so the harness can select / exclude them:

```python
import pytest

pytestmark = pytest.mark.integration
```

### Missing prerequisites — FAIL, do not skip

Integration tests **must fail loudly** when an external dependency or its credentials are missing. Do NOT use `pytest.skip(...)` to paper over a missing cookie, an unreachable PostgreSQL, or a missing API key. A silent skip is the test version of `except: pass` — the suite reports green while the most important checks did not run.

```python
import os

PG_DSN = os.environ.get("PG_DSN")
if not PG_DSN:
    raise RuntimeError(
        "PG_DSN env var is required for DB integration tests. "
        "Set it to a valid PostgreSQL connection string."
    )
```

If a credential is "test infrastructure" (CI secret, env var), its absence is a CI misconfiguration that the operator must fix — not a condition the test should tolerate. Same for an unreachable external host: if `nominatim.openstreetmap.org` is down, the suite goes red. Do not normalize a yellow-but-green CI.

### Test the *intended* behavior, never the *current* behavior — MANDATORY

Tests assert what the contract says **should** happen — API spec, function docstring, domain convention. They do NOT assert whatever the current (possibly buggy) implementation happens to do. If the impl is wrong, the test fails. That failure is the signal — it is the entire point of having tests.

Wrong:

```python
# Endpoint silently returns empty list when Nominatim is unreachable.
# This pins current behavior so future changes are visible.
assert result["country_code"] == ""
```

Right:

```python
# Unreachable geocoder must surface as an error, not silent empty string
assert "error" in result or result["country_code"] is None
```

The right version fails today against a broken implementation. When the implementation is fixed, the test starts passing automatically. Do **not** soften assertions to keep the suite green when the production code is wrong.

### Two-step thinking — MANDATORY before writing any code

Same discipline as unit tests, but the requirements describe **cross-boundary outcomes**:

**Step 1 — extract integration requirements:**
- **Business purpose**: what observable cross-service outcome does this flow enable?
- **Boundary contract**: what crosses the seam? (HTTP request/response, DB row, Anki card, browser cookie)
- **Persisted state**: what row/document must exist after success? what must NOT exist on failure?
- **Idempotency**: if the operation is retried with the same inputs, does the system end up in the same state?
- **Failure modes**: what happens when the downstream dependency is slow / returns 5xx / is unreachable?
- **What must NOT change on failure paths**: partial writes, orphan records, leaked connections

**Step 2 — only then, write tests** that prove each requirement against real infrastructure.

### Test naming — MANDATORY

Same shape as unit tests: `test_{what_should_happen}_when_{condition}`. Describe the observable cross-boundary outcome.

```python
# WRONG — mechanics, repeats the function name, or generic
def test_save_game_returns_none(): ...
def test_sync_runs(): ...
def test_reverse_geocode_success(): ...

# RIGHT — describes the boundary outcome
def test_round_is_persisted_in_postgres_when_sync_completes(): ...
def test_country_data_is_upserted_when_yaml_is_valid(): ...
def test_no_partial_game_is_saved_when_enrichment_fails(): ...
def test_second_identical_game_creates_no_duplicate_when_retried(): ...
```

### Structure — Arrange-Act-Assert (MANDATORY)

```python
@pytest.mark.asyncio
async def test_round_is_persisted_in_postgres_when_sync_completes(
    pg_pool, cleanup_games,
):
    # Arrange
    game_id = f"itest-{uuid.uuid4().hex[:8]}"
    token = f"token-{uuid.uuid4().hex[:6]}"

    # Act
    result = await sync_game(token, game_id)
    cleanup_games.append((token, game_id))

    # Assert
    assert result is not None
    row = await pg_pool.fetchrow(
        "SELECT 1 FROM game WHERE challenge_token = $1 AND game_id = $2",
        token, game_id,
    )
    assert row is not None
```

Three rules tighter than unit tests:
1. **Arrange may reach into infrastructure directly** (raw SQL insert is fine for setup).
2. **Act must use the public boundary** — the function under test, not its internals.
3. **Assert must verify observable state** — the return value AND the side effects (DB row, external API call). The success of the call is not the contract; the persisted state is.

### Real dependencies — never stub the seam under test

| Dependency | Integration test policy |
|------------|-------------------------|
| PostgreSQL | **Real** — connect via asyncpg pool |
| GeoGuessr API | **Real** — requires valid `_ncfa` cookie. Missing credentials → test **fails**, not skips. |
| Nominatim | **Real** — rate-limited to 1 req/s |
| AnkiConnect | **Real** — requires Anki running with AnkiConnect plugin |
| Playwright / browser | **Real** — launches headless browser |

Stubbing the very thing the test exists to verify defeats the test. If you find yourself wanting to stub PostgreSQL in an integration test, the test you actually need is a unit test.

### Per-test data ownership — MANDATORY

Each test creates the data it needs with values unique to itself (UUIDs, timestamps) and asserts only on data it created. **Never share fixture data between tests.** Never depend on "the seed row from setup".

```python
# RIGHT — every test owns a fresh game_id and token
game_id = f"itest-{uuid.uuid4().hex[:8]}"
token = f"token-{uuid.uuid4().hex[:6]}"
```

This is the single biggest predictor of a suite that survives past 50 tests without flaking. Shared state is where flakiness is born.

### Cleanup via fixtures with `try / finally` — MANDATORY

Cleanup runs **even when the test fails**. Use a fixture that yields a list/handle the test appends to; teardown drains it.

```python
@pytest.fixture
def cleanup_games(pg_pool) -> Iterator[list[tuple[str, str]]]:
    created: list[tuple[str, str]] = []
    try:
        yield created
    finally:
        for token, game_id in created:
            pg_pool.execute(
                "DELETE FROM round WHERE challenge_token = $1 AND game_id = $2",
                token, game_id,
            )
            pg_pool.execute(
                "DELETE FROM game WHERE challenge_token = $1 AND game_id = $2",
                token, game_id,
            )
```

Never `del`-on-success. Cleanup-on-failure is where flakiness compounds — the test that fails leaves residue that breaks the next test, and the team chases ghost failures.

**Forbidden cleanup patterns:**
- Truncating shared tables (deletes data other tests rely on)
- Restarting PostgreSQL (destroys all local dev data)
- Manual cleanup at the end of the test body (skipped on failure)

### No `sleep()` — poll with a deadline

`time.sleep(N)` is a confession that you don't know when the system is ready. On a slow CI runner it is also how every test eventually goes flaky.

```python
# WRONG
await start_workflow()
await asyncio.sleep(5)
assert (await db.find_one(...)) is not None

# RIGHT
await start_workflow()
async def row_persisted() -> bool:
    row = await pool.fetchrow("SELECT 1 FROM game WHERE ...")
    return row is not None

await _await_until(row_persisted, timeout=10, interval=0.2)


async def _await_until(predicate, timeout: float, interval: float = 0.2) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")
```

### One real flow per test — one reason to fail

Integration tests are slow. The temptation to bundle "while we're at it, also check X" is strong and always wrong. Each test exercises **one cross-boundary flow** and asserts the outcome plus its invariants. If you want to test two flows, write two tests.

### Test the boundaries that matter

For every flow, the gold-standard set looks like this:

1. **Happy path** — the real end-to-end flow succeeds and persists the expected state
2. **Primary failure path** — the most common rejection (validation error, missing cookie, 4xx from API)
3. **Idempotency** — the same operation, repeated, ends in the same state (no duplicate writes)
4. **Partial-failure invariant** — when a downstream step fails, no orphan / partial state remains
5. (Where relevant) **Concurrency** — two simultaneous identical requests; assert exactly one effect

Three to five tests per flow is typical. Don't pad — slow noise is worse than no coverage.

### Public-API assertions only (act + assert)

| Phase | What is allowed |
|-------|-----------------|
| Arrange | Direct SQL inserts, raw env var sets — whatever fastest sets up the precondition |
| Act     | **Public boundary only**: function call, API call |
| Assert  | **Observable state only**: return value, DB row via SQL query, external API side effect |

Reaching into internal module state (`service._cache`, `workflow._step`) makes the test brittle to refactors that did not break behavior. The boundary IS the contract.

### Coverage target — 3 to 5 tests per flow

1. **Happy path** — real end-to-end success, verify persisted state
2. **Primary failure** — most common rejection, verify error envelope AND no partial state
3. **Idempotency** — same request twice, one effect
4. (Optional) **Boundary** — empty, max size, unicode, already-exists, not-found
5. (Optional) **Partial failure** — downstream step fails mid-flow; verify rollback / no orphan

Don't pad past 5 unless the flow genuinely has more distinct behaviors. Don't drop below 3 if the flow is non-trivial.

### Output shape

- `pytestmark = pytest.mark.integration` at module top
- Module-level fail if external prerequisites are missing
- Top-level test functions, **no classes**
- Top-level imports only
- Fixtures with `try / finally` cleanup, even when the test passes
- Black formatting, line length 100
- Runnable standalone: `pytest tests/integration/<path>/<file>.py -v`

### Imports — match the `src` package root

Same rule as unit tests: drop the `src/` prefix, use the remainder as the dotted import path. pytest config has `pythonpath = ["src"]`.

| Source | Test import |
|--------|-------------|
| `src/core/sync.py` | `from core.sync import sync_game` |
| `src/core/geo_enrich.py` | `from core.geo_enrich import reverse_geocode` |
| `src/db/db.py` | `from db.db import save_game` |

---

## Code Example

```python
import uuid
from typing import Iterator

import pytest
import asyncpg

from core.sync import sync_game
from db.db import init_pool, close_pool


pytestmark = pytest.mark.integration

PG_DSN = "postgresql://localhost:5432/geoguessr_trainer"


@pytest.fixture(scope="session")
async def pg_pool() -> asyncpg.Pool:
    await init_pool()
    yield
    await close_pool()


@pytest.fixture
def cleanup_games(pg_pool) -> Iterator[list[tuple[str, str]]]:
    created: list[tuple[str, str]] = []
    try:
        yield created
    finally:
        for token, game_id in created:
            async with pg_pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM round WHERE challenge_token = $1 AND game_id = $2",
                    token, game_id,
                )
                await conn.execute(
                    "DELETE FROM game WHERE challenge_token = $1 AND game_id = $2",
                    token, game_id,
                )


@pytest.mark.asyncio
async def test_round_is_persisted_in_postgres_when_sync_completes(
    pg_pool, cleanup_games, valid_cookie,
):
    # Arrange
    token = f"itest-{uuid.uuid4().hex[:6]}"
    game_id = f"g-{uuid.uuid4().hex[:8]}"

    # Act
    result = await sync_game(token, game_id)
    cleanup_games.append((token, game_id))

    # Assert — game row exists
    assert result is not None
    row = await pg_pool.fetchrow(
        "SELECT 1 FROM game WHERE challenge_token = $1 AND game_id = $2",
        token, game_id,
    )
    assert row is not None


@pytest.mark.asyncio
async def test_no_duplicate_game_when_same_token_is_synced_twice(
    pg_pool, cleanup_games, valid_cookie,
):
    # Arrange
    token = f"itest-{uuid.uuid4().hex[:6]}"
    game_id = f"g-{uuid.uuid4().hex[:8]}"

    # Act — sync the same game twice
    first = await sync_game(token, game_id)
    second = await sync_game(token, game_id)
    cleanup_games.append((token, game_id))

    # Assert — exactly one game row
    assert first is not None
    assert second is not None
    count = await pg_pool.fetchval(
        "SELECT count(*) FROM game WHERE challenge_token = $1 AND game_id = $2",
        token, game_id,
    )
    assert count == 1
```

---

## Workflow When Asked to Write an Integration Test

1. **Identify the boundary.** What seam does this exercise? GeoGuessr API, Nominatim, PostgreSQL, AnkiConnect, Playwright? The boundary determines the act phase.
2. **List the real dependencies the test needs.** PostgreSQL, valid _ncfa cookie, Nominatim reachable, Anki running. Confirm they are available.
3. **Write the integration requirements** (Business purpose / Boundary contract / Persisted state / Idempotency / Failure modes / What must NOT change) as inline reasoning, before any code.
4. **Pick 3–5 tests** mapped to the requirements: happy path, primary failure, idempotency, (optional) boundary, (optional) partial failure.
5. **Write the file** at `tests/integration/<module-path>/<flow>.py` with:
   - `pytestmark = pytest.mark.integration`
   - module-level fail for missing prerequisites
   - fixtures with `try / finally` cleanup
   - unique data per test (UUIDs)
   - poll-until helpers instead of `sleep`
   - public-boundary act phase, observable-state assertions
6. **Run it:** `pytest tests/integration/<path>/<flow>.py -v --tb=short -m integration`. If it fails, fix the test or the production code, never the assertion to make it green.

---

## Commands

```bash
# Run a single integration test
pytest tests/integration/core/sync/full_game_sync.py -v --tb=short -m integration

# Run every integration test
pytest tests/integration/ -v -m integration

# Run only NON-integration (i.e. unit) tests
pytest tests/unit/ -v -m "not integration"
```

No container wrappers — tests run directly against the source tree and real infrastructure (PostgreSQL, GeoGuessr API, Nominatim).

---

## Anti-Patterns — Do Not Do These

- ❌ Stubbing the very dependency the test exists to exercise (`patch("db.db._pool")` in an integration test — that is a unit test wearing a costume)
- ❌ `time.sleep(N)` to wait for async work — always poll with a deadline
- ❌ Sharing fixture data across tests ("the seed row from conftest")
- ❌ Cleanup in the test body instead of a fixture — it is skipped on failure and breaks the next test
- ❌ Truncating shared tables or restarting PostgreSQL to "reset state"
- ❌ Bundling multiple unrelated flows into one test "because it is already slow"
- ❌ Calling the function under test's internals instead of going through its real boundary
- ❌ `pytest.skip(...)` to paper over missing credentials / unreachable dependencies — missing infra → loud failure
- ❌ Asserting `Mock.assert_called_with(...)` — there should be no Mock at the seam being tested

---

## Resources

- **Sibling skill (unit tests)**: [`skills/unit-test/SKILL.md`](../unit-test/SKILL.md)
- **pytest config**: in `pyproject.toml` — `pythonpath = ["src"]`, `testpaths = ["tests"]`
