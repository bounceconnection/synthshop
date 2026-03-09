# SynthShop

## Overview

CLI tool for identifying synths/eurorack modules from photos and listing them on Reverb. Uses Claude Vision for identification, ModularGrid for eurorack verification, and the Reverb API for listings and pricing.

## Tech Stack

- Python 3.11+ with type hints throughout
- **CLI:** Typer + Rich (formatted terminal output, Kitty graphics protocol for images)
- **Data:** Pydantic models, JSON file storage in `products/`
- **APIs:** Anthropic (Claude Vision), Reverb (HAL+JSON), ModularGrid (HTML scraping), DuckDuckGo (search fallback)
- **Testing:** pytest with respx/moto for HTTP/AWS mocking, 148 tests, pylint 10/10
- **Build:** Hatchling, editable install via `pip install -e ".[dev]"`

## Project Structure

```
src/synthshop/
  cli/commands/     identify.py, publish.py, list.py, unpublish.py
  cli/prompts.py    Claude Vision prompt templates (identify + panel detect)
  core/             models.py, config.py, product_store.py
  integrations/     claude_vision.py, modulargrid.py, reverb.py
tests/              9 test files, 148 tests (all APIs mocked)
products/           JSON product files
```

## Development

```bash
pip install -e ".[dev]"   # Install with dev deps
pytest                     # Run all tests
pytest -v -k "modulargrid" # Run specific tests
pylint src/synthshop/      # Lint (must be 10/10)
```

## Key Patterns

- Claude Vision uses **tool calling** for structured output (not free-text parsing)
- ModularGrid search uses a **4-tier fallback**: direct URL > DDG search > DDG with make+model > parallel brute-force across 50+ manufacturers
- DDG slug ranking uses `_pick_best_slug()` to avoid wrong-module matches on short/ambiguous names
- Custom panel detection **never attributes a maker** — only flags as "Custom/aftermarket panel"
- Reverb client has **exponential backoff** on 429 rate limits
- All changes must pass `pylint` with 10/10 score
