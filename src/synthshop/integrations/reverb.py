"""Documented Reverb draft/update contract and bounded anonymous listing research."""

import re
import time
from urllib.parse import urlparse

import httpx

from synthshop.core.config import Settings

HEADERS = {
    "Accept": "application/hal+json",
    "Content-Type": "application/hal+json",
    "Accept-Version": "3.0",
    "User-Agent": "SynthShop/0.2 (local listing assistant)",
    "X-Display-Currency": "USD",
}
NOT_CREATED = frozenset({401, 403, 422})


class ReverbAPIError(ValueError):
    """Sanitized provider failure; raw bodies can contain private account data."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ReverbClient:
    """No redirects and no automatic replay of writes, even after a timeout."""

    def __init__(self, settings: Settings, *, authenticated: bool = False):
        self.settings = settings
        headers = dict(HEADERS)
        if authenticated:
            headers["Authorization"] = "Bearer " + settings.require_reverb()
        self.client = httpx.Client(
            base_url=settings.reverb_base_url + "/",
            headers=headers,
            timeout=35,
            follow_redirects=False,
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.client.close()

    def request(self, method: str, route: str, **kwargs) -> dict:
        """Only fixed relative API routes; credentials never follow external links."""
        if route.startswith("/") or ":" in route or ".." in route:
            raise ValueError("Invalid provider route")
        response = self.client.request(method, route, **kwargs)
        if method == "GET" and response.status_code == 429:
            delay = response.headers.get("Retry-After", "1")
            time.sleep(min(float(delay) if delay.isdigit() else 1, 5))
            response = self.client.request(method, route, **kwargs)
        if response.is_error or response.is_redirect:
            fields = []
            try:
                errors = response.json().get("errors", {})
                if isinstance(errors, dict):
                    fields = [key for key in errors if re.fullmatch(r"[a-z_]+", key)]
            except ValueError:
                pass
            hint = " Check fields: " + ", ".join(fields) if fields else ""
            raise ReverbAPIError(
                f"Reverb {method} failed (HTTP {response.status_code}).{hint} "
                "Check account permissions and listing requirements on Reverb.",
                response.status_code,
            )
        result = response.json()
        if not isinstance(result, dict):
            raise ReverbAPIError("Unexpected Reverb response; operation not verified.")
        return result

    def search(self, query: str, *, sold: bool) -> list[dict]:
        """Sold flag is publicly observed, not a paid-transaction contract."""
        params = {"query": query, "per_page": 40}
        if sold:
            params["show_only_sold"] = "true"
        return self.request("GET", "listings", params=params).get("listings", [])

    def references(self) -> dict:
        """Resolve current identifiers, never map slugs or swapped static condition UUIDs."""
        regions = self.request("GET", "shipping/regions")["shipping_regions"]
        flattened = []

        def visit(rows):
            for row in rows:
                flattened.append({"code": row["code"], "name": row["name"]})
                visit(row.get("children", []))

        visit(regions)
        return {
            "categories": self.request("GET", "categories/flat")["categories"],
            "conditions": self.request("GET", "listing_conditions")["conditions"],
            "regions": flattened,
        }

    def verify_shop(self) -> dict:
        """Authenticated profile and shop reads; stable ID and canonical slug must agree."""
        self.request("GET", "my/account")
        shop = self.request("GET", "shop")
        shop = shop.get("shop", shop)
        url = shop.get("_links", {}).get("web", {}).get("href", "")
        slug = shop.get("slug") or url.rstrip("/").rsplit("/", 1)[-1]
        if (
            str(shop.get("id")) != self.settings.expected_shop_id
            or slug != self.settings.expected_shop_slug
        ):
            raise ReverbAPIError(
                "Token-associated shop does not match the configured target. "
                "No listing was authorized for this account."
            )
        return {"id": str(shop["id"]), "slug": slug, "name": shop.get("name", slug)}

    def own_listings(self, sku: str) -> list[dict]:
        """Exact SKU/state checks; an empty lookup is never permission for another create."""
        result = self.request("GET", "my/listings", params={"sku": sku, "state": "all"})
        return [row for row in result.get("listings", []) if row.get("sku") == sku]

    def get_listing(self, listing_id: str) -> dict:
        """Read a known remote ID, with path validation."""
        if not listing_id.isdigit():
            raise ReverbAPIError("Unexpected remote listing ID")
        result = self.request("GET", f"listings/{listing_id}")
        return result.get("listing", result)

    def verify_ownership(self, listing: dict, sku: str) -> None:
        """Require both returned shop ownership and membership in authenticated own listings."""
        if str(listing.get("shop_id")) != self.settings.expected_shop_id:
            raise ReverbAPIError(
                "Returned listing ownership mismatch; no further write is allowed."
            )
        rows = self.own_listings(sku)
        if len(rows) != 1 or str(rows[0].get("id")) != str(listing.get("id")):
            raise ReverbAPIError("Cannot uniquely verify listing in authenticated own listings.")

    def create_draft(self, payload: dict) -> dict:
        """Exactly one POST per persisted attempt; publication is a separate operation.

        Only a NOT_CREATED status proves no listing exists; anything else stays ambiguous.
        """
        result = self.request("POST", "listings", json={**payload, "publish": False})
        return result.get("listing", result)

    def update(self, listing_id: str, payload: dict) -> None:
        """Documented update/publish operation, with no automatic retry."""
        if not listing_id.isdigit():
            raise ReverbAPIError("Invalid remote listing ID")
        self.request("PUT", f"listings/{listing_id}", json=payload)

    def public_url(self, listing: dict) -> str:
        """Do not expose provider-controlled non-Reverb links as successful results."""
        url = listing.get("_links", {}).get("web", {}).get("href", "")
        expected = (
            "sandbox.reverb.com" if "sandbox" in self.settings.reverb_base_url else "reverb.com"
        )
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != expected
            or not parsed.path.startswith("/item/")
        ):
            raise ReverbAPIError("Live listing URL has not been verified.")
        return url
