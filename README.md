# SynthShop

CLI tool for identifying synthesizers and eurorack modules from photos and listing them on Reverb.

Takes photos of your gear, identifies it using Claude Vision, cross-references with ModularGrid for eurorack modules, checks Reverb for market pricing, and publishes listings — all from the command line.

## Features

- **Photo identification** — Claude Vision identifies make, model, condition, and generates listing descriptions
- **ModularGrid verification** — Corrects misidentified eurorack manufacturers and pulls accurate descriptions/features from ModularGrid (parallel search across 48 manufacturers)
- **Reverb pricing** — Looks up active listings to replace estimated prices with real market data
- **One-command publishing** — Identify, upload images, and create a Reverb listing in a single step

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and add your API keys:

```bash
cp .env.example .env
```

Required keys:
- `ANTHROPIC_API_KEY` — from [console.anthropic.com](https://console.anthropic.com)
- `REVERB_API_TOKEN` — from Reverb > Settings > API & Integrations

## Usage

### Identify a synth from photos

```bash
synthshop identify photo1.jpg photo2.jpg
```

Options:
- `--model`, `-m` — Claude model to use (default: `claude-sonnet-4-20250514`)
- `--no-modulargrid` — Skip ModularGrid verification

### Publish a listing to Reverb

```bash
synthshop publish photos/*.jpg --price 250
```

Options:
- `--price` — Listing price (required)
- `--make` / `--model` — Skip photo identification, specify manually
- `--condition` — Override condition (Mint, Excellent, Very Good, Good, Fair, Poor)
- `--live` — Publish immediately instead of creating a draft
- `--skip-reverb` — Save product locally without creating a Reverb listing

### List products

```bash
synthshop list
synthshop list --status sold
```

### Mark as sold or unpublish

```bash
synthshop sold <product-id>
synthshop unpublish <product-id>
```

## Project Structure

```
src/synthshop/
  core/           Models, config, product store (JSON files)
  integrations/   Claude Vision, ModularGrid, Reverb API
  cli/            Typer commands and prompts
tests/            124 tests with mocked external APIs
products/         Product JSON files (the "database")
```

## Tests

All external APIs (Anthropic, Reverb, ModularGrid) are mocked — no API keys needed to run tests.

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/test_modulargrid.py

# Run a specific test class or method
pytest tests/test_identify_modulargrid.py::TestVerifyWithModularGrid::test_replaces_description

# Run tests matching a keyword
pytest -k "modulargrid"

# Run with coverage report
pytest --cov=synthshop --cov-report=term-missing
```

### Test files

| File | Covers |
|------|--------|
| `test_claude_vision.py` | Image encoding, content blocks, API tool use |
| `test_modulargrid.py` | HTML extraction, slug search, parallel batch lookup |
| `test_identify_modulargrid.py` | ModularGrid correction of description, features, manufacturer |
| `test_reverb.py` | Reverb API client, rate limiting, listing CRUD, image upload |
| `test_cli.py` | CLI commands (identify, publish, list, unpublish, sold) |
| `test_models.py` | Pydantic models and validation |
| `test_product_store.py` | JSON product CRUD |
| `test_config.py` | Settings and env var loading |
