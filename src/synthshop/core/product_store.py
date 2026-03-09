"""CRUD operations for product JSON files in the products/ directory."""

import json
from pathlib import Path

from synthshop.core.config import settings
from synthshop.core.models import Product, ProductStatus


class ProductStore:
    """Read and write Product JSON files.

    Each product is stored as a single JSON file named {id}.json in the
    products directory. This class provides CRUD operations over that directory.
    """

    def __init__(self, products_dir: Path | None = None):
        self.products_dir = products_dir or settings.products_dir
        self.products_dir.mkdir(parents=True, exist_ok=True)

    def save(self, product: Product) -> Path:
        """Save a product to its JSON file. Creates or overwrites."""
        product.touch()
        path = product.json_path(self.products_dir)
        path.write_text(product.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    def load(self, product_id: str) -> Product:
        """Load a product by ID. Raises FileNotFoundError if not found."""
        path = self.products_dir / f"{product_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Product not found: {product_id}")
        return Product.model_validate_json(path.read_text(encoding="utf-8"))

    def list_all(self) -> list[Product]:
        """Load all products, sorted by creation date (newest first)."""
        products = []
        for path in self.products_dir.glob("*.json"):
            try:
                products.append(Product.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                # Skip malformed files rather than crashing
                continue
        return sorted(products, key=lambda p: p.created_at, reverse=True)

    def list_by_status(self, status: ProductStatus) -> list[Product]:
        """Load products filtered by status."""
        return [p for p in self.list_all() if p.status == status]

    def delete(self, product_id: str) -> None:
        """Delete a product's JSON file. Raises FileNotFoundError if not found."""
        path = self.products_dir / f"{product_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Product not found: {product_id}")
        path.unlink()

    def exists(self, product_id: str) -> bool:
        """Check if a product exists by ID."""
        return (self.products_dir / f"{product_id}.json").exists()
