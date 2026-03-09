#!/usr/bin/env python3
"""Fetch Reverb categories and conditions and cache them locally.

Usage:
    python scripts/seed_categories.py
    python scripts/seed_categories.py --sandbox  # use sandbox API

Requires REVERB_API_TOKEN to be set in .env or environment.
"""

import argparse
from pathlib import Path

from synthshop.integrations.reverb import ReverbClient, save_reference_data

DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "synthshop" / "data"


def main():
    parser = argparse.ArgumentParser(description="Fetch and cache Reverb reference data.")
    parser.add_argument(
        "--sandbox",
        action="store_true",
        help="Use the Reverb sandbox API instead of production.",
    )
    args = parser.parse_args()

    base_url = "https://sandbox.reverb.com/api" if args.sandbox else None

    with ReverbClient(base_url=base_url) as client:
        print("Fetching categories...")
        categories = client.get_categories_flat()
        print(f"  Got {len(categories)} categories.")

        print("Fetching conditions...")
        conditions = client.get_conditions()
        print(f"  Got {len(conditions)} conditions.")

    save_reference_data(categories, conditions, DATA_DIR)
    print(f"\nSaved to {DATA_DIR}/")
    print(f"  categories.json ({len(categories)} entries)")
    print(f"  conditions.json ({len(conditions)} entries)")


if __name__ == "__main__":
    main()
