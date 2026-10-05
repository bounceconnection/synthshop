# SynthShop

## Overview

Local website with a CLI launcher for identifying music gear from photos, researching prices, reviewing editable drafts, and explicitly approving Reverb publication. Uses Claude Vision and the Reverb API; restricted marketplace evidence is entered manually.

## Tech Stack

- Python 3.11+ with type hints throughout
- **CLI:** Typer + Rich launcher; FastAPI/Jinja2/Uvicorn loopback website
- **Data:** Pydantic models, Decimal money, revisioned SQLite and managed local photos
- **APIs:** Anthropic (Claude Vision), Reverb (HAL+JSON), private R2 signed-URL image staging
- **Testing:** pytest with isolated storage/credentials and provider doubles; pylint must be 10/10
- **Build:** Hatchling, editable install via `pip install -e ".[dev]"`

## Project Structure

```
src/synthshop/
  cli/main.py       serve launcher command
  web/              FastAPI routes, Jinja templates, local styles
  core/             models, config, SQLite store, photos, pricing, application, publishing
  integrations/     claude_vision.py, reverb.py, staging.py
tests/              Behavioral safety and recovery tests (no live credentials)
products/           Legacy JSON import source; originals retained
```

## Development

```bash
pip install -e ".[dev]"   # Install with dev deps
pytest                     # Run all tests
pytest -v -k "reverb"     # Run publication/recovery tests
pylint src/synthshop/      # Lint (must be 10/10)
```

## Key Patterns

- Claude Vision uses **tool calling** for structured output (not free-text parsing)
- Reverb sold-tagged displayed prices remain distinct from active asks and confirmed transactions
- ModularGrid/DDG scraping was removed; manual observations retain provenance and match review
- The vision prompt flags custom/aftermarket panels **without attributing a maker**; there is no separate panel-detection pass
- Reverb reads honor bounded 429 retry; writes are never automatically replayed after ambiguity
- All changes must pass `pylint` with 10/10 score
