"""Product data model — the central data contract for SynthShop.

Every product (synth, drum machine, effects pedal, etc.) is represented as a
Product instance. This model is serialized to/from JSON files in products/.
"""

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field


class ProductStatus(StrEnum):
    """Lifecycle status of a product listing."""

    DRAFT = "draft"
    LISTED = "listed"
    SOLD = "sold"
    UNLISTED = "unlisted"


class Condition(StrEnum):
    """Item condition (aligns with Reverb's condition options)."""

    MINT = "Mint"
    EXCELLENT = "Excellent"
    VERY_GOOD = "Very Good"
    GOOD = "Good"
    FAIR = "Fair"
    POOR = "Poor"
    NON_FUNCTIONING = "Non Functioning"


class PriceRange(BaseModel):
    """Estimated market price range from Claude Vision identification."""

    low: float
    high: float
    currency: str = "USD"


class ReverbListing(BaseModel):
    """Reverb-specific listing data, populated after publishing to Reverb."""

    listing_id: int
    slug: str | None = None
    url: str | None = None
    state: str | None = None  # "draft", "live", "sold", "ended"


class StripeListing(BaseModel):
    """Stripe-specific data, populated after creating a payment link."""

    payment_link_id: str
    payment_link_url: str
    price_id: str | None = None
    product_id: str | None = None


class Product(BaseModel):
    """A product listing for a synth, drum machine, or other music gear.

    This is the central data model shared by the CLI, website, and all
    integrations. Serialized as JSON files in the products/ directory.
    """

    # Identity
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    status: ProductStatus = ProductStatus.DRAFT

    # Item details (populated by Claude Vision identification or manual input)
    make: str
    model: str
    year: int | None = None
    variant: str | None = None  # e.g. "Rev 2", "Desktop Module", "60-key"
    category: str | None = None  # Reverb category slug
    description: str = ""
    features: list[str] = Field(default_factory=list)
    condition: Condition = Condition.GOOD
    condition_notes: str = ""

    # Pricing
    price: float  # Asking price in USD
    price_range: PriceRange | None = None  # Estimated market range
    shipping_price: float = 0.0
    offers_enabled: bool = True

    # Images
    image_urls: list[str] = Field(default_factory=list)  # Public URLs (R2)
    local_image_paths: list[str] = Field(default_factory=list)  # Original local paths

    # Platform listings
    reverb: ReverbListing | None = None
    stripe: StripeListing | None = None

    @property
    def title(self) -> str:
        """Generate a listing title from make/model/year/variant."""
        parts = [self.make, self.model]
        if self.variant:
            parts.append(self.variant)
        if self.year:
            parts.append(f"({self.year})")
        return " ".join(parts)

    @property
    def json_filename(self) -> str:
        """Filename for this product's JSON file."""
        return f"{self.id}.json"

    def touch(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = datetime.now()

    def json_path(self, products_dir: Path) -> Path:
        """Full path to this product's JSON file."""
        return products_dir / self.json_filename
