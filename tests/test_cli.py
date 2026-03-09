"""Tests for CLI commands."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from synthshop.cli.main import app
from synthshop.core.models import Condition, Product, ProductStatus, ReverbListing
from synthshop.core.product_store import ProductStore
from synthshop.integrations.claude_vision import SynthIdentification

runner = CliRunner()


@pytest.fixture
def jpeg_image(tmp_path) -> Path:
    img = tmp_path / "synth.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100 + b"\xff\xd9")
    return img


@pytest.fixture
def store(tmp_path) -> ProductStore:
    return ProductStore(products_dir=tmp_path)


@pytest.fixture
def sample_identification() -> SynthIdentification:
    return SynthIdentification(
        make="Roland",
        model="Juno-106",
        year=1984,
        variant=None,
        category="synthesizers",
        description="The legendary Juno-106 analog polysynth.",
        features=["6-voice analog", "DCO oscillators", "Classic chorus", "MIDI"],
        condition="Excellent",
        condition_notes="Minor wear on case.",
        price_low=1100.0,
        price_high=1600.0,
        confidence="high",
        notes="Check voice chips.",
    )


@pytest.fixture
def sample_reverb_listing() -> ReverbListing:
    return ReverbListing(
        listing_id=12345,
        slug="roland-juno-106",
        url="https://reverb.com/item/12345-roland-juno-106",
        state="draft",
    )


# --- identify command ---


class TestIdentifyCommand:
    @patch("synthshop.cli.commands.identify.identify_from_photos")
    def test_identify_success(self, mock_identify, jpeg_image, sample_identification):
        mock_identify.return_value = sample_identification
        result = runner.invoke(app, ["identify", str(jpeg_image)])
        assert result.exit_code == 0
        assert "Roland" in result.output
        assert "Juno-106" in result.output
        assert "1984" in result.output
        assert "1,100" in result.output
        assert "1,600" in result.output

    def test_identify_missing_file(self, tmp_path):
        result = runner.invoke(app, ["identify", str(tmp_path / "nope.jpg")])
        assert result.exit_code == 1
        assert "not found" in result.output

    @patch("synthshop.cli.commands.identify.identify_from_photos")
    def test_identify_api_error(self, mock_identify, jpeg_image):
        mock_identify.side_effect = RuntimeError("API error")
        result = runner.invoke(app, ["identify", str(jpeg_image)])
        assert result.exit_code == 1
        assert "failed" in result.output.lower()


# --- publish command ---


class TestPublishCommand:
    @patch("synthshop.cli.commands.publish.ProductStore")
    @patch("synthshop.cli.commands.publish.ReverbClient")
    @patch("synthshop.cli.commands.publish.identify")
    def test_publish_with_identification(
        self, mock_identify, mock_reverb_cls, mock_store_cls,
        jpeg_image, sample_identification, sample_reverb_listing, tmp_path,
    ):
        mock_identify.return_value = sample_identification

        mock_client = MagicMock()
        mock_client.create_listing.return_value = sample_reverb_listing
        mock_client.upload_images.return_value = [{"id": 1}]
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_reverb_cls.return_value = mock_client

        mock_store = MagicMock()
        mock_store.save.return_value = tmp_path / "abc.json"
        mock_store_cls.return_value = mock_store

        result = runner.invoke(
            app,
            ["publish", str(jpeg_image), "--price", "1400"],
            input="y\n",
        )
        assert result.exit_code == 0
        assert "Saved" in result.output
        assert "reverb.com" in result.output

        # Verify product was built correctly
        saved_product = mock_store.save.call_args[0][0]
        assert saved_product.make == "Roland"
        assert saved_product.model == "Juno-106"
        assert saved_product.price == 1400.0

    @patch("synthshop.cli.commands.publish.ProductStore")
    @patch("synthshop.cli.commands.publish.ReverbClient")
    def test_publish_manual_input(
        self, mock_reverb_cls, mock_store_cls,
        jpeg_image, sample_reverb_listing, tmp_path,
    ):
        mock_client = MagicMock()
        mock_client.create_listing.return_value = sample_reverb_listing
        mock_client.upload_images.return_value = [{"id": 1}]
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_reverb_cls.return_value = mock_client

        mock_store = MagicMock()
        mock_store.save.return_value = tmp_path / "abc.json"
        mock_store_cls.return_value = mock_store

        result = runner.invoke(
            app,
            [
                "publish", str(jpeg_image),
                "--price", "800",
                "--make", "Korg",
                "--model", "Minilogue",
                "--condition", "Good",
            ],
        )
        assert result.exit_code == 0
        assert "Korg Minilogue" in result.output

        saved_product = mock_store.save.call_args[0][0]
        assert saved_product.make == "Korg"
        assert saved_product.model == "Minilogue"
        assert saved_product.price == 800.0

    def test_publish_make_without_model(self, jpeg_image):
        result = runner.invoke(
            app,
            ["publish", str(jpeg_image), "--price", "500", "--make", "Korg"],
        )
        assert result.exit_code == 1
        assert "Both --make and --model" in result.output

    @patch("synthshop.cli.commands.publish.ProductStore")
    def test_publish_skip_reverb(self, mock_store_cls, jpeg_image, tmp_path):
        mock_store = MagicMock()
        mock_store.save.return_value = tmp_path / "abc.json"
        mock_store_cls.return_value = mock_store

        result = runner.invoke(
            app,
            [
                "publish", str(jpeg_image),
                "--price", "600",
                "--make", "Roland",
                "--model", "SH-101",
                "--skip-reverb",
            ],
        )
        assert result.exit_code == 0
        assert "Saved" in result.output
        # Should not mention Reverb URL
        assert "reverb.com" not in result.output

    @patch("synthshop.cli.commands.publish.identify")
    def test_publish_user_rejects_identification(self, mock_identify, jpeg_image, sample_identification):
        mock_identify.return_value = sample_identification
        result = runner.invoke(
            app,
            ["publish", str(jpeg_image), "--price", "1400"],
            input="n\n",
        )
        assert result.exit_code == 0
        assert "Aborted" in result.output


# --- list command ---


class TestListCommand:
    @patch("synthshop.cli.commands.list.ProductStore")
    def test_list_empty(self, mock_store_cls):
        mock_store_cls.return_value.list_all.return_value = []
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "No products" in result.output

    @patch("synthshop.cli.commands.list.ProductStore")
    def test_list_with_products(self, mock_store_cls):
        products = [
            Product(
                make="Roland", model="Juno-106", price=1400.0,
                status=ProductStatus.LISTED,
                reverb=ReverbListing(
                    listing_id=123,
                    url="https://reverb.com/item/123",
                ),
            ),
            Product(
                make="Moog", model="Sub 37", price=1500.0,
                status=ProductStatus.DRAFT,
            ),
        ]
        mock_store_cls.return_value.list_all.return_value = products
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "Juno-106" in result.output
        assert "Sub 37" in result.output
        assert "1,400" in result.output

    @patch("synthshop.cli.commands.list.ProductStore")
    def test_list_filter_by_status(self, mock_store_cls):
        mock_store_cls.return_value.list_by_status.return_value = []
        result = runner.invoke(app, ["list", "--status", "sold"])
        assert result.exit_code == 0
        mock_store_cls.return_value.list_by_status.assert_called_once_with(ProductStatus.SOLD)


# --- unpublish command ---


class TestUnpublishCommand:
    @patch("synthshop.cli.commands.unpublish.ReverbClient")
    @patch("synthshop.cli.commands.unpublish.ProductStore")
    def test_unpublish_with_reverb(self, mock_store_cls, mock_reverb_cls):
        product = Product(
            id="abc123",
            make="Roland", model="Juno-106", price=1400.0,
            status=ProductStatus.LISTED,
            reverb=ReverbListing(listing_id=12345, url="https://reverb.com/item/12345"),
        )
        mock_store = MagicMock()
        mock_store.load.return_value = product
        mock_store_cls.return_value = mock_store

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_reverb_cls.return_value = mock_client

        result = runner.invoke(app, ["unpublish", "abc123"])
        assert result.exit_code == 0
        assert "unlisted" in result.output

        mock_client.end_listing.assert_called_once_with(12345)
        saved = mock_store.save.call_args[0][0]
        assert saved.status == ProductStatus.UNLISTED

    @patch("synthshop.cli.commands.unpublish.ProductStore")
    def test_unpublish_not_found(self, mock_store_cls):
        mock_store_cls.return_value.load.side_effect = FileNotFoundError("not found")
        result = runner.invoke(app, ["unpublish", "nope"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


# --- sold command ---


class TestSoldCommand:
    @patch("synthshop.cli.commands.unpublish.ReverbClient")
    @patch("synthshop.cli.commands.unpublish.ProductStore")
    def test_sold_ends_reverb_listing(self, mock_store_cls, mock_reverb_cls):
        product = Product(
            id="abc123",
            make="Roland", model="Juno-106", price=1400.0,
            status=ProductStatus.LISTED,
            reverb=ReverbListing(listing_id=12345, url="https://reverb.com/item/12345"),
        )
        mock_store = MagicMock()
        mock_store.load.return_value = product
        mock_store_cls.return_value = mock_store

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_reverb_cls.return_value = mock_client

        result = runner.invoke(app, ["sold", "abc123"])
        assert result.exit_code == 0
        assert "sold" in result.output

        mock_client.end_listing.assert_called_once_with(12345)
        saved = mock_store.save.call_args[0][0]
        assert saved.status == ProductStatus.SOLD

    @patch("synthshop.cli.commands.unpublish.ProductStore")
    def test_sold_draft_product(self, mock_store_cls):
        """Sold on a draft product doesn't try to end Reverb listing."""
        product = Product(
            id="abc123",
            make="Korg", model="MS-20", price=900.0,
            status=ProductStatus.DRAFT,
            reverb=ReverbListing(listing_id=99999, url="https://reverb.com/item/99999"),
        )
        mock_store = MagicMock()
        mock_store.load.return_value = product
        mock_store_cls.return_value = mock_store

        result = runner.invoke(app, ["sold", "abc123"])
        assert result.exit_code == 0

        saved = mock_store.save.call_args[0][0]
        assert saved.status == ProductStatus.SOLD
