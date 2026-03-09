"""Reverb API client (HAL+JSON, bearer auth).

Reverb API docs: https://reverb.com/page/api
All responses use application/hal+json. Pagination uses `_links.next.href`.
Rate limiting returns 429 — we retry with exponential backoff.
"""

import json
import time
from pathlib import Path
from typing import Any

import httpx

from synthshop.core.config import settings
from synthshop.core.models import Condition, Product, ReverbListing

# Reverb condition UUIDs — mapped from our Condition enum
# These are the standard Reverb condition UUIDs used in the API
CONDITION_UUIDS: dict[Condition, str] = {
    Condition.MINT: "f7a3f48c-972a-44c6-b01a-0cd27488d3ab",
    Condition.EXCELLENT: "ae4d9114-1bd7-4ec5-a4ba-6653af5ac84d",
    Condition.VERY_GOOD: "df268ad1-c462-4ba6-b6db-e007e23922ea",
    Condition.GOOD: "9225283f-60c0-4b15-87e5-aa6e40891700",
    Condition.FAIR: "c44a1df4-a2ca-423b-9e03-a48d0850f22c",
    Condition.POOR: "ac5b9c3e-eb2c-4c47-b28a-b4e4b8c021b0",
    Condition.NON_FUNCTIONING: "fbf35668-96a0-4baa-bcde-ab18d6b1b329",
}

DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3
BACKOFF_BASE = 1.0  # seconds


class ReverbAPIError(Exception):
    """Raised when the Reverb API returns an error response."""

    def __init__(self, status_code: int, message: str, body: Any = None):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Reverb API error {status_code}: {message}")


class ReverbClient:
    """Client for the Reverb API.

    Uses bearer token auth and application/hal+json content type.
    Handles rate limiting with exponential backoff on 429 responses.
    """

    def __init__(
        self,
        token: str | None = None,
        base_url: str | None = None,
    ):
        self.token = token or settings.require_reverb()
        self.base_url = (base_url or settings.reverb_base_url).rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/hal+json",
                "Accept": "application/hal+json",
                "Accept-Version": "3.0",
            },
            timeout=DEFAULT_TIMEOUT,
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make an API request with retry on 429 rate limits.

        Returns the parsed JSON response body.
        Raises ReverbAPIError on non-2xx responses (after retries for 429).
        """
        for attempt in range(MAX_RETRIES + 1):
            response = self._client.request(method, path, **kwargs)

            if response.status_code == 429:
                if attempt < MAX_RETRIES:
                    wait = BACKOFF_BASE * (2**attempt)
                    time.sleep(wait)
                    continue
                raise ReverbAPIError(429, "Rate limited after max retries")

            if response.status_code == 204:
                return {}

            body = response.json() if response.content else {}

            if not response.is_success:
                message = body.get("message", response.reason_phrase or "Unknown error")
                raise ReverbAPIError(response.status_code, message, body)

            return body

        raise ReverbAPIError(429, "Rate limited after max retries")

    # --- Listings ---

    def create_listing(self, product: Product, *, live: bool = False) -> ReverbListing:
        """Create a new listing on Reverb from a Product.

        Args:
            product: The product to list.
            live: If True, publish immediately. If False, create as draft.

        Returns:
            ReverbListing with the listing ID and URL.
        """
        payload = self._product_to_listing_payload(product, publish=live)
        data = self._request("POST", "/listings", json=payload)
        listing = data.get("listing", data)
        return ReverbListing(
            listing_id=listing["id"],
            slug=listing.get("slug"),
            url=listing.get("_links", {}).get("web", {}).get("href"),
            state=self._extract_state(listing),
        )

    @staticmethod
    def _extract_state(listing: dict) -> str | None:
        """Extract listing state slug from Reverb's varying response format."""
        state = listing.get("state")
        if isinstance(state, dict):
            return state.get("slug")
        return state

    def update_listing(self, listing_id: int, product: Product) -> ReverbListing:
        """Update an existing Reverb listing.

        Args:
            listing_id: The Reverb listing ID to update.
            product: The product with updated fields.

        Returns:
            Updated ReverbListing.
        """
        payload = self._product_to_listing_payload(product, publish=False)
        data = self._request("PUT", f"/listings/{listing_id}", json=payload)
        listing = data.get("listing", data)
        return ReverbListing(
            listing_id=listing["id"],
            slug=listing.get("slug"),
            url=listing.get("_links", {}).get("web", {}).get("href"),
            state=self._extract_state(listing),
        )

    def publish_listing(self, listing_id: int) -> dict:
        """Change a draft listing to live/published state."""
        return self._request("PUT", f"/listings/{listing_id}/state/publish")

    def end_listing(self, listing_id: int) -> dict:
        """End/remove a listing."""
        return self._request("PUT", f"/listings/{listing_id}/state/end")

    def upload_images(self, listing_id: int, image_paths: list[Path]) -> list[dict]:
        """Upload images directly to a Reverb listing.

        Reverb accepts multipart file uploads to POST /listings/{id}/images.
        Images are uploaded one at a time (Reverb doesn't support batch upload).

        Args:
            listing_id: The Reverb listing ID to attach images to.
            image_paths: Local file paths to upload (JPEG, PNG, etc.).

        Returns:
            List of image response dicts from Reverb.

        Raises:
            FileNotFoundError: If any image path doesn't exist.
        """
        # Use a separate httpx client for multipart uploads — the main client
        # has Content-Type: application/hal+json which conflicts with multipart.
        upload_client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/hal+json",
            },
            timeout=DEFAULT_TIMEOUT,
        )

        results = []
        try:
            for path in image_paths:
                if not path.exists():
                    raise FileNotFoundError(f"Image not found: {path}")

                with open(path, "rb") as f:
                    response = upload_client.post(
                        f"/listings/{listing_id}/images",
                        files={"file": (path.name, f, "image/jpeg")},
                    )

                    if response.status_code == 429:
                        time.sleep(BACKOFF_BASE)
                        f.seek(0)
                        response = upload_client.post(
                            f"/listings/{listing_id}/images",
                            files={"file": (path.name, f, "image/jpeg")},
                        )

                    if not response.is_success:
                        body = response.json() if response.content else {}
                        message = body.get("message", response.reason_phrase or "Upload failed")
                        raise ReverbAPIError(response.status_code, message, body)

                    results.append(response.json() if response.content else {})
        finally:
            upload_client.close()

        return results

    def get_listing(self, listing_id: int) -> dict:
        """Fetch a single listing by ID."""
        return self._request("GET", f"/listings/{listing_id}")

    def get_my_listings(self, *, page: int = 1, per_page: int = 50) -> dict:
        """Fetch the authenticated user's listings.

        Returns the raw HAL+JSON response including `listings` array and
        `_links` for pagination.
        """
        return self._request(
            "GET",
            "/my/listings",
            params={"page": page, "per_page": per_page},
        )

    # --- Price lookup ---

    def search_listings(self, query: str, *, per_page: int = 10) -> list[dict]:
        """Search Reverb listings by query string.

        Returns a list of listing dicts with price info.
        """
        data = self._request(
            "GET",
            "/listings",
            params={"query": query, "per_page": per_page},
        )
        return data.get("listings", [])

    def get_price_guide(self, query: str) -> dict | None:
        """Get market price range for an item by searching active Reverb listings.

        Returns dict with low, high, count — or None if no listings found.
        """
        listings = self.search_listings(query, per_page=20)
        if not listings:
            return None

        prices = []
        for listing in listings:
            price_data = listing.get("price", {})
            amount = price_data.get("amount")
            if amount:
                try:
                    prices.append(float(amount))
                except (ValueError, TypeError):
                    continue

        if not prices:
            return None

        return {
            "low": min(prices),
            "high": max(prices),
            "median": sorted(prices)[len(prices) // 2],
            "count": len(prices),
        }

    # --- Reference data ---

    def get_categories_flat(self) -> list[dict]:
        """Fetch all Reverb categories as a flat list.

        Returns list of dicts with 'uuid', 'full_name', 'slug', etc.
        """
        data = self._request("GET", "/categories/flat")
        return data.get("categories", [])

    def get_conditions(self) -> list[dict]:
        """Fetch listing condition options.

        Returns list of dicts with 'uuid', 'display_name', 'slug'.
        """
        data = self._request("GET", "/listings/conditions")
        return data.get("conditions", [])

    # --- Payload construction ---

    def _product_to_listing_payload(self, product: Product, *, publish: bool) -> dict:
        """Convert a Product to a Reverb API listing payload."""
        # Build description with features list
        description = product.description
        if product.features:
            feature_list = "\n".join(f"• {f}" for f in product.features)
            description = f"{description}\n\nKey Features:\n{feature_list}"

        payload: dict[str, Any] = {
            "make": product.make,
            "model": product.model,
            "title": product.title,
            "description": description,
            "condition": {
                "uuid": CONDITION_UUIDS.get(product.condition, CONDITION_UUIDS[Condition.GOOD]),
            },
            "price": {
                "amount": str(product.price),
                "currency": "USD",
            },
            "has_inventory": True,
            "inventory": 1,
            "offers_enabled": product.offers_enabled,
            "publish": publish,
        }

        if product.year:
            payload["year"] = str(product.year)

        if product.category:
            payload["categories"] = [{"uuid": product.category}]

        if product.shipping_price > 0:
            payload["shipping"] = {
                "rates": [
                    {
                        "rate": {
                            "amount": str(product.shipping_price),
                            "currency": "USD",
                        },
                        "region_code": "US_CON",
                    }
                ]
            }

        if product.condition_notes:
            payload["description"] = (
                f"{payload['description']}\n\nCondition: {product.condition_notes}"
            )

        return payload


def save_reference_data(categories: list[dict], conditions: list[dict], data_dir: Path) -> None:
    """Save fetched reference data to local JSON cache files."""
    data_dir.mkdir(parents=True, exist_ok=True)

    categories_path = data_dir / "categories.json"
    categories_path.write_text(
        json.dumps(categories, indent=2) + "\n", encoding="utf-8"
    )

    conditions_path = data_dir / "conditions.json"
    conditions_path.write_text(
        json.dumps(conditions, indent=2) + "\n", encoding="utf-8"
    )


def load_cached_categories(data_dir: Path) -> list[dict] | None:
    """Load cached categories from local JSON, or None if not cached."""
    path = data_dir / "categories.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_cached_conditions(data_dir: Path) -> list[dict] | None:
    """Load cached conditions from local JSON, or None if not cached."""
    path = data_dir / "conditions.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
