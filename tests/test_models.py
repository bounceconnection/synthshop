"""Tests for the Product model."""

from datetime import datetime

import pytest

from synthshop.core.models import (
    Condition,
    PriceRange,
    Product,
    ProductStatus,
    ReverbListing,
    StripeListing,
)


class TestProduct:
    def test_create_minimal(self):
        """Product can be created with just make, model, and price."""
        p = Product(make="Roland", model="Juno-106", price=1200.0)
        assert p.make == "Roland"
        assert p.model == "Juno-106"
        assert p.price == 1200.0
        assert p.status == ProductStatus.DRAFT
        assert p.condition == Condition.GOOD
        assert len(p.id) == 12
        assert isinstance(p.created_at, datetime)

    def test_create_full(self):
        """Product with all fields populated."""
        p = Product(
            make="Sequential",
            model="Prophet-6",
            year=2020,
            variant="Desktop Module",
            category="synths",
            description="Pristine condition Prophet-6 desktop module.",
            features=["6-voice polyphonic", "Analog VCOs", "Distortion"],
            condition=Condition.MINT,
            condition_notes="Like new, barely used.",
            price=2400.0,
            price_range=PriceRange(low=2200.0, high=2800.0),
            shipping_price=50.0,
            offers_enabled=True,
            image_urls=["https://r2.example.com/img1.jpg"],
            local_image_paths=["/photos/img1.jpg"],
            reverb=ReverbListing(listing_id=12345, url="https://reverb.com/item/12345"),
            stripe=StripeListing(
                payment_link_id="plink_123",
                payment_link_url="https://buy.stripe.com/abc",
            ),
        )
        assert p.year == 2020
        assert p.variant == "Desktop Module"
        assert p.price_range.high == 2800.0
        assert p.reverb.listing_id == 12345
        assert p.stripe.payment_link_url == "https://buy.stripe.com/abc"

    def test_title_basic(self):
        p = Product(make="Moog", model="Subsequent 37", price=1500.0)
        assert p.title == "Moog Subsequent 37"

    def test_title_with_variant_and_year(self):
        p = Product(make="Roland", model="Juno-106", year=1984, variant="MIJ", price=1200.0)
        assert p.title == "Roland Juno-106 MIJ (1984)"

    def test_title_with_year_only(self):
        p = Product(make="Korg", model="MS-20", year=1978, price=900.0)
        assert p.title == "Korg MS-20 (1978)"

    def test_json_filename(self):
        p = Product(id="abc123def456", make="Roland", model="SH-101", price=800.0)
        assert p.json_filename == "abc123def456.json"

    def test_touch_updates_timestamp(self):
        p = Product(make="Roland", model="SH-101", price=800.0)
        original = p.updated_at
        p.touch()
        assert p.updated_at >= original

    def test_serialization_roundtrip(self):
        """Product survives JSON serialization and deserialization."""
        p = Product(
            make="Dave Smith",
            model="OB-6",
            year=2016,
            price=2200.0,
            features=["6-voice", "Analog"],
            condition=Condition.EXCELLENT,
        )
        json_str = p.model_dump_json()
        restored = Product.model_validate_json(json_str)
        assert restored.make == p.make
        assert restored.model == p.model
        assert restored.year == p.year
        assert restored.price == p.price
        assert restored.features == p.features
        assert restored.condition == Condition.EXCELLENT
        assert restored.id == p.id


class TestProductStatus:
    def test_status_values(self):
        assert ProductStatus.DRAFT == "draft"
        assert ProductStatus.LISTED == "listed"
        assert ProductStatus.SOLD == "sold"
        assert ProductStatus.UNLISTED == "unlisted"


class TestCondition:
    def test_condition_values(self):
        assert Condition.MINT == "Mint"
        assert Condition.NON_FUNCTIONING == "Non Functioning"


class TestPriceRange:
    def test_defaults_to_usd(self):
        pr = PriceRange(low=100.0, high=200.0)
        assert pr.currency == "USD"
