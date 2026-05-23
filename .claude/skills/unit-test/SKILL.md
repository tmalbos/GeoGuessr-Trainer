---
name: unit-test
description: >
  Writes behavior-driven pytest unit tests for GeoGuessr Trainer Python code. Tests
  document business outcomes (what the caller can rely on), not implementation.
  Trigger: when the user asks for a unit test, asks to test a function, or pastes
  a function and asks "what would the test look like".
metadata:
  author: tmalbos
  version: "1.0"
---

## When to Use

- Adding a unit test for an existing function (API client, analyzer, DB helper, enrichment)
- You modified a function and want a regression test for its observable behavior
- Reviewing an existing test and rewriting it to follow the conventions below

**Do not** use this skill for:
- Integration tests (real GeoGuessr API, real PostgreSQL, real Nominatim) — those have their own conventions
- E2E tests
- Test scaffolding (fixtures, conftest) — those are project-level

---

## Critical Patterns

### File location — MANDATORY

Tests live under `tests/unit/<module-path>/<function_name>.py`.

| Source file | Test file |
|-------------|-----------|
| `src/core/analyzer.py` → `process_round` | `tests/unit/core/analyzer/process_round.py` |
| `src/core/api.py` → `fetch_game_results` | `tests/unit/core/api/fetch_game_results.py` |
| `src/core/geo_enrich.py` → `reverse_geocode` | `tests/unit/core/geo_enrich/reverse_geocode.py` |
| `src/core/stats.py` → `compute_performance` | `tests/unit/core/stats/compute_performance.py` |
| `src/core/auth.py` → `get_cookie` | `tests/unit/core/auth/get_cookie.py` |
| `src/db/db.py` → `fetch_country_geo_signals` | `tests/unit/db/db/fetch_country_geo_signals.py` |
| `src/db/update_country.py` → `sync_country_data` | `tests/unit/db/update_country/sync_country_data.py` |
| `src/anki/generator.py` → `generate_cards` | `tests/unit/anki/generator/generate_cards.py` |
| `src/anki/anki_connect.py` → `invoke` | `tests/unit/anki/anki_connect/invoke.py` |
| `src/i18n/lang.py` → `translate` | `tests/unit/i18n/lang/translate.py` |

The `<module-path>` is the relative path inside `src/` (drop the `src/` prefix). Function name becomes the **filename** — one file per tested function.

### Skip trivial functions

Do not write tests for functions that have no testable business logic:

- Empty body / `pass` only
- Single `return <constant>` (e.g. `return None`, `return ""`, `return True`)
- Single `return <name>` or `return self.attr` (bare getters with no computation)
- Docstring-only bodies

If the function only has these, **return without writing a test** and say so.

### Two-step thinking — MANDATORY before writing any code

Before writing the test file, work through these in order:

**Step 1 — extract business requirements** (what, not how):
- **Business purpose**: one sentence — what business outcome does this function enable?
- **Behavioral contracts**: input → outcome pairs the caller can rely on (both success AND failure)
- **Domain invariants**: things that must NEVER be violated regardless of path (e.g. "distance must never be negative")
- **Boundary conditions**: empty / zero / None / negative / max / duplicates / already-exists — for each, state the input AND the expected outcome
- **What must NOT change**: side effects that must NOT occur on partial/failed paths

Write these requirements in plain language. Never describe loops, conditionals, or algorithm steps.

**Step 2 — only then, generate tests** that prove each requirement holds.

### Test naming — MANDATORY

Format: `test_{what_should_happen}_when_{condition}`

- Describe observable business outcomes, not internal mechanics
- A non-engineer reading the test name must understand what breaks
- **Never include the function name itself in the test name**

```python
# WRONG — describes mechanics, repeats function name, or uses magic numbers
def test_process_round_returns_dict(): ...
def test_process_round_works(): ...
def test_score_5000(): ...

# RIGHT — describes business behavior
def test_score_is_0_when_guess_is_in_wrong_country(): ...
def test_round_enriches_with_ecoregion_when_coordinates_are_valid(): ...
def test_anki_card_is_skipped_when_country_already_has_cards(): ...
```

### Structure — Arrange-Act-Assert (MANDATORY)

Every test uses AAA with **explicit comment labels** and a blank line between sections:

```python
def test_transfer_fails_when_balance_is_insufficient():
    # Arrange
    origin = Account(balance=100)
    destination = Account(balance=0)
    amount = 150

    # Act
    result = origin.transfer(amount, destination)

    # Assert
    assert result.is_error()
    assert result.error == ErrorType.INSUFFICIENT_BALANCE
    assert origin.balance == 100       # invariant: balance unchanged
    assert destination.balance == 0    # invariant: destination unchanged
```

Add **invariant assertions** — things that must NOT have changed after the operation.

### One behavior per test

One test = one observable behavior. A single behavior may require multiple asserts to fully verify; group them in one test rather than splitting them into meaningless fragments.

```python
# WRONG — fragments that lose meaning in isolation
def test_returns_error(): ...
def test_country_unchanged(): ...
def test_round_not_saved(): ...

# RIGHT — one atomic behavior, fully verified
def test_failed_enrichment_leaves_round_unsaved():
    # All three asserts describe one behavior
```

### Test doubles — use the right type

| Type  | Use when |
|-------|----------|
| Stub  | Control what a dependency returns (`return_value=...`). Do NOT assert it was called. |
| Mock  | Verify a call WAS made AND control its return value. Use ONLY when the interaction itself is the behavior under test. |
| Fake  | Simplified real implementation (e.g. an in-memory dict instead of PostgreSQL). |
| Dummy | Required parameter that has no effect on this test — pass `None` or a sentinel. |

**GeoGuessr-specific rules:**
- `asyncpg` connection/query methods → **Stub only** (`return_value=...`). Do not assert they were called unless the call itself is the contract.
- `httpx.AsyncClient` (GeoGuessr API, Nominatim) → always **Stub** or use `respx` to mock HTTP.
- Playwright → always **Stub**.
- `Mock.assert_called_with(...)` is appropriate only when "we called X with Y" is the business contract being tested.
- **Pydantic/dataclass models** → use **real instances**. Never mock them.

### Property-style assertions

Prefer expressing a law over hardcoding a specific output:

```python
# Weaker — depends on a magic number
assert calculate_total(price=10, qty=2) == 20

# Stronger — expresses the law
price = 10
qty = 2
assert calculate_total(price=price, qty=qty) == price * qty
```

Variable names should make the relationship readable without a comment.

### Coverage target — 4 to 6 tests per function

1. **Happy path** — normal successful case
2. **Primary failure case** — most common error / rejection
3. **2+ boundary conditions** drawn from the requirements (empty / None / zero / negative / max / duplicate / already-exists)
4. **1+ invariant check** — something that must NOT change on failure

Don't pad past 6 unless the function genuinely has more distinct behaviors. Don't drop below 4 unless the function is unusually narrow.

### Output shape

- Top-level test functions, **no classes**
- Top-level imports only, no inline imports inside tests
- No markdown fences, no explanation outside `# Arrange`, `# Act`, `# Assert`, and one-line invariant notes
- Black formatting, line length 100
- The test file must be runnable standalone: `pytest tests/unit/<module-path>/<function>.py`

### Imports — match the `src` package root

pytest is configured with `pythonpath = ["src"]` in `pyproject.toml`, so tests import using the path relative to `src/`:

| Source file | Test import |
|-------------|-------------|
| `src/core/analyzer.py` | `from core.analyzer import process_round` |
| `src/core/api.py` | `from core.api import fetch_game_results` |
| `src/core/geo_enrich.py` | `from core.geo_enrich import reverse_geocode` |
| `src/core/stats.py` | `from core.stats import compute_performance` |
| `src/core/auth.py` | `from core.auth import get_cookie` |
| `src/db/db.py` | `from db.db import fetch_country_geo_signals` |
| `src/db/update_country.py` | `from db.update_country import sync_country_data` |
| `src/anki/generator.py` | `from anki.generator import generate_cards` |
| `src/anki/anki_connect.py` | `from anki.anki_connect import invoke` |
| `src/i18n/lang.py` | `from i18n.lang import translate` |

---

## Code Example

A complete, well-shaped test file:

```python
from unittest.mock import AsyncMock, patch

import pytest

from core.analyzer import process_round


def test_score_is_0_when_guess_is_in_wrong_country():
    # Arrange
    round_data = {
        "round_number": 1,
        "score": 0,
        "distance_km": 5000.0,
        "real_geo": {"country_code": "MX", "lat": 23.0, "lng": -102.0},
        "guess_geo": {"country_code": "US", "lat": 38.0, "lng": -97.0},
    }

    # Act
    result = process_round(round_data)

    # Assert
    assert result["score"] == 0
    assert result["distance_km"] == 5000.0
    assert result["real_geo"]["country_code"] == "MX"
    assert result["guess_geo"]["country_code"] == "US"


def test_round_is_skipped_when_coordinates_are_missing():
    # Arrange
    round_data = {
        "round_number": 1,
        "real_geo": {"country_code": "MX", "lat": None, "lng": None},
        "guess_geo": None,
    }

    # Act & Assert
    with pytest.raises(ValueError, match="missing coordinates"):
        process_round(round_data)


@pytest.mark.asyncio
async def test_country_geo_signals_returns_roads_and_plates():
    # Arrange
    mock_road = {"road_line_id": 1, "rule": "whole-country", "outer_color": "white"}
    mock_plate = {"license_plate_id": 1, "car_type": "normal", "front_color": "white"}

    with patch("db.db._pool") as mock_pool:
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetch.side_effect = [[mock_road], [mock_plate]]

        # Act
        result = await fetch_country_geo_signals("DE")

        # Assert
        assert len(result["roads"]) == 1
        assert result["roads"][0]["outer_color"] == "white"
        assert len(result["license_plates"]) == 1
        assert result["license_plates"][0]["car_type"] == "normal"


@pytest.mark.asyncio
async def test_empty_result_when_country_has_no_signals():
    # Arrange
    with patch("db.db._pool") as mock_pool:
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetch.side_effect = [[], []]

        # Act
        result = await fetch_country_geo_signals("XX")

        # Assert
        assert result == {"roads": [], "license_plates": []}
```

---

## Workflow When Asked to Write a Test

1. **Read the function source.** Open the file. Read decorators, signature, body, return annotation.
2. **Read its direct dependencies** — helpers it calls, constants it uses, models it accepts.
3. **Triviality check.** If the function is a bare getter, pass, or constant return → stop, no test.
4. **Write the requirements** (Business purpose / Behavioral contracts / Invariants / Boundaries / What must NOT change) **as inline reasoning**, before any code.
5. **Pick 4–6 tests** mapped to the requirements: happy path, primary failure, ≥2 boundaries, ≥1 invariant.
6. **Write the file** at `tests/unit/<module-path>/<function_name>.py` following the AAA + naming + stub rules.
7. **Run it:** `pytest tests/unit/<module-path>/<function>.py -v --tb=short`. Iterate on the test, not the production code, when assertions are wrong.

If the function under test calls something the test cannot easily isolate (GeoGuessr API, Nominatim, AnkiConnect, Playwright), stub it — do not rewrite the function to make it testable unless the user asks.

---

## Commands

```bash
# Run a single unit test
pytest tests/unit/core/analyzer/process_round.py -v --tb=short

# Run all unit tests
pytest tests/unit/ -v

# Run only unit tests (no integration)
pytest tests/unit/ -v -m "not integration"
```

No container wrappers — tests run directly against the source tree.

---

## Anti-Patterns — Do Not Do These

- ❌ Test names that repeat the function name (`test_process_round_works`)
- ❌ Asserting `db.fetch_one.assert_called_with(...)` unless the call IS the contract
- ❌ Mocking dataclasses / Pydantic models
- ❌ One assertion per test, fragmenting one behavior across multiple tests
- ❌ Hardcoded magic numbers in assertions when a property/law applies
- ❌ Tests for trivial getters / `return None` / `pass` bodies
- ❌ Inline imports, classes wrapping tests, fixtures with no reuse
- ❌ Comments narrating what the next line does (the AAA labels and clear names are enough)

---

## Resources

- **pytest config**: in `pyproject.toml` — `pythonpath = ["src"]`, `testpaths = ["tests"]`
- **Ruff**: `ruff check .` (line length 100, configured in pyproject.toml)
- **Mypy**: `mypy src/` (strict mode off, `warn_return_any = true`)
