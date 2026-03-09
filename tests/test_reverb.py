"""Tests for the Reverb API client (mocked HTTP)."""

import json
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from synthshop.core.models import Condition, Product, ReverbListing
from synthshop.integrations.reverb import (
    CONDITION_UUIDS,
    ReverbAPIError,
    ReverbClient,
    load_cached_categories,
    load_cached_conditions,
    save_reference_data,
)

FAKE_TOKEN = "test-reverb-token"
BASE_URL = "https://api.reverb.com/api"


@pytest.fixture
def client():
    """ReverbClient with a fake token, no settings lookup."""
    c = ReverbClient(token=FAKE_TOKEN, base_url=BASE_URL)
    yield c
    c.close()


@pytest.fixture
def sample_product():
    """A product ready to be listed."""
    return Product(
        make="Roland",
        model="Juno-106",
        year=1984,
        description="Classic analog polysynth in excellent condition.",
        features=["6-voice analog", "DCO oscillators", "Classic chorus"],
        condition=Condition.EXCELLENT,
        condition_notes="Minor wear on case edges.",
        price=1400.0,
        shipping_price=75.0,
        offers_enabled=True,
        image_urls=["https://r2.example.com/juno1.jpg", "https://r2.example.com/juno2.jpg"],
    )


def _listing_response(listing_id: int = 12345, state: str = "draft") -> dict:
    """Build a mock Reverb listing response."""
    return {
        "listing": {
            "id": listing_id,
            "slug": "roland-juno-106",
            "state": {"slug": state},
            "_links": {
                "web": {"href": f"https://reverb.com/item/{listing_id}-roland-juno-106"},
            },
        }
    }


# --- Request / retry tests ---


class TestRequest:
    @respx.mock
    def test_auth_header(self, client):
        """Requests include the bearer token and HAL+JSON headers."""
        route = respx.get(f"{BASE_URL}/my/listings").mock(
            return_value=httpx.Response(200, json={"listings": []})
        )
        client.get_my_listings()
        request = route.calls[0].request
        assert request.headers["authorization"] == f"Bearer {FAKE_TOKEN}"
        assert "hal+json" in request.headers["accept"]

    @respx.mock
    def test_429_retries_with_backoff(self, client):
        """429 responses trigger retries with exponential backoff."""
        route = respx.get(f"{BASE_URL}/my/listings")
        route.side_effect = [
            httpx.Response(429),
            httpx.Response(429),
            httpx.Response(200, json={"listings": []}),
        ]
        with patch("synthshop.integrations.reverb.time.sleep") as mock_sleep:
            result = client.get_my_listings()
        assert result == {"listings": []}
        assert mock_sleep.call_count == 2
        # Exponential backoff: 1s, 2s
        mock_sleep.assert_any_call(1.0)
        mock_sleep.assert_any_call(2.0)

    @respx.mock
    def test_429_max_retries_exceeded(self, client):
        """After MAX_RETRIES 429 responses, raise ReverbAPIError."""
        respx.get(f"{BASE_URL}/my/listings").mock(
            return_value=httpx.Response(429)
        )
        with patch("synthshop.integrations.reverb.time.sleep"):
            with pytest.raises(ReverbAPIError, match="Rate limited"):
                client.get_my_listings()

    @respx.mock
    def test_error_response(self, client):
        """Non-2xx responses raise ReverbAPIError with status and message."""
        respx.get(f"{BASE_URL}/listings/99999").mock(
            return_value=httpx.Response(
                404,
                json={"message": "Listing not found"},
            )
        )
        with pytest.raises(ReverbAPIError) as exc_info:
            client.get_listing(99999)
        assert exc_info.value.status_code == 404
        assert "Listing not found" in str(exc_info.value)

    @respx.mock
    def test_204_returns_empty_dict(self, client):
        """204 No Content responses return an empty dict."""
        respx.put(f"{BASE_URL}/listings/123/state/end").mock(
            return_value=httpx.Response(204)
        )
        result = client.end_listing(123)
        assert result == {}


# --- Listing CRUD tests ---


class TestCreateListing:
    @respx.mock
    def test_create_draft(self, client, sample_product):
        route = respx.post(f"{BASE_URL}/listings").mock(
            return_value=httpx.Response(201, json=_listing_response())
        )
        result = client.create_listing(sample_product, live=False)

        assert isinstance(result, ReverbListing)
        assert result.listing_id == 12345
        assert result.slug == "roland-juno-106"
        assert result.state == "draft"
        assert "reverb.com" in result.url

        # Verify payload
        payload = json.loads(route.calls[0].request.content)
        assert payload["make"] == "Roland"
        assert payload["model"] == "Juno-106"
        assert payload["title"] == "Roland Juno-106 (1984)"
        assert payload["price"]["amount"] == "1400.0"
        assert payload["publish"] is False
        assert payload["year"] == "1984"
        assert payload["offers_enabled"] is True

    @respx.mock
    def test_create_live(self, client, sample_product):
        respx.post(f"{BASE_URL}/listings").mock(
            return_value=httpx.Response(201, json=_listing_response(state="live"))
        )
        result = client.create_listing(sample_product, live=True)
        assert result.state == "live"

    @respx.mock
    def test_payload_includes_features_in_description(self, client, sample_product):
        route = respx.post(f"{BASE_URL}/listings").mock(
            return_value=httpx.Response(201, json=_listing_response())
        )
        client.create_listing(sample_product)
        payload = json.loads(route.calls[0].request.content)
        assert "Key Features:" in payload["description"]
        assert "• 6-voice analog" in payload["description"]
        assert "• DCO oscillators" in payload["description"]

    @respx.mock
    def test_payload_includes_condition_notes(self, client, sample_product):
        route = respx.post(f"{BASE_URL}/listings").mock(
            return_value=httpx.Response(201, json=_listing_response())
        )
        client.create_listing(sample_product)
        payload = json.loads(route.calls[0].request.content)
        assert "Condition: Minor wear on case edges." in payload["description"]

    @respx.mock
    def test_payload_condition_uuid(self, client, sample_product):
        route = respx.post(f"{BASE_URL}/listings").mock(
            return_value=httpx.Response(201, json=_listing_response())
        )
        client.create_listing(sample_product)
        payload = json.loads(route.calls[0].request.content)
        assert payload["condition"]["uuid"] == CONDITION_UUIDS[Condition.EXCELLENT]

    @respx.mock
    def test_payload_shipping(self, client, sample_product):
        route = respx.post(f"{BASE_URL}/listings").mock(
            return_value=httpx.Response(201, json=_listing_response())
        )
        client.create_listing(sample_product)
        payload = json.loads(route.calls[0].request.content)
        rates = payload["shipping"]["rates"]
        assert len(rates) == 1
        assert rates[0]["rate"]["amount"] == "75.0"
        assert rates[0]["region_code"] == "US_CON"

    @respx.mock
    def test_minimal_product(self, client):
        """Product with no optional fields still produces a valid payload."""
        p = Product(make="Korg", model="Minilogue", price=400.0)
        route = respx.post(f"{BASE_URL}/listings").mock(
            return_value=httpx.Response(201, json=_listing_response(listing_id=999))
        )
        result = client.create_listing(p)
        assert result.listing_id == 999

        payload = json.loads(route.calls[0].request.content)
        assert "year" not in payload
        assert "categories" not in payload
        assert "shipping" not in payload
        assert "photos" not in payload


class TestUploadImages:
    @respx.mock
    def test_upload_single_image(self, client, tmp_path):
        img = tmp_path / "synth.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100 + b"\xff\xd9")

        respx.post(f"{BASE_URL}/listings/12345/images").mock(
            return_value=httpx.Response(201, json={"id": 1, "url": "https://images.reverb.com/1.jpg"})
        )
        results = client.upload_images(12345, [img])
        assert len(results) == 1
        assert results[0]["id"] == 1

    @respx.mock
    def test_upload_multiple_images(self, client, tmp_path):
        imgs = []
        for i in range(3):
            img = tmp_path / f"synth{i}.jpg"
            img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50 + b"\xff\xd9")
            imgs.append(img)

        respx.post(f"{BASE_URL}/listings/12345/images").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        results = client.upload_images(12345, imgs)
        assert len(results) == 3

    def test_upload_missing_file_raises(self, client, tmp_path):
        with pytest.raises(FileNotFoundError, match="Image not found"):
            client.upload_images(12345, [tmp_path / "nope.jpg"])

    @respx.mock
    def test_upload_api_error(self, client, tmp_path):
        img = tmp_path / "synth.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50 + b"\xff\xd9")

        respx.post(f"{BASE_URL}/listings/12345/images").mock(
            return_value=httpx.Response(422, json={"message": "Invalid image"})
        )
        with pytest.raises(ReverbAPIError, match="Invalid image"):
            client.upload_images(12345, [img])


class TestUpdateListing:
    @respx.mock
    def test_update(self, client, sample_product):
        respx.put(f"{BASE_URL}/listings/12345").mock(
            return_value=httpx.Response(200, json=_listing_response())
        )
        result = client.update_listing(12345, sample_product)
        assert result.listing_id == 12345


class TestPublishAndEnd:
    @respx.mock
    def test_publish(self, client):
        respx.put(f"{BASE_URL}/listings/123/state/publish").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        result = client.publish_listing(123)
        assert result == {"status": "ok"}

    @respx.mock
    def test_end(self, client):
        respx.put(f"{BASE_URL}/listings/123/state/end").mock(
            return_value=httpx.Response(204)
        )
        result = client.end_listing(123)
        assert result == {}


class TestGetListing:
    @respx.mock
    def test_get_existing(self, client):
        respx.get(f"{BASE_URL}/listings/12345").mock(
            return_value=httpx.Response(200, json=_listing_response()["listing"])
        )
        result = client.get_listing(12345)
        assert result["id"] == 12345


class TestGetMyListings:
    @respx.mock
    def test_pagination_params(self, client):
        route = respx.get(f"{BASE_URL}/my/listings").mock(
            return_value=httpx.Response(200, json={"listings": []})
        )
        client.get_my_listings(page=2, per_page=25)
        assert route.calls[0].request.url.params["page"] == "2"
        assert route.calls[0].request.url.params["per_page"] == "25"


# --- Reference data tests ---


class TestReferenceData:
    @respx.mock
    def test_get_categories_flat(self, client):
        categories = [{"uuid": "abc", "full_name": "Synthesizers", "slug": "synthesizers"}]
        respx.get(f"{BASE_URL}/categories/flat").mock(
            return_value=httpx.Response(200, json={"categories": categories})
        )
        result = client.get_categories_flat()
        assert len(result) == 1
        assert result[0]["slug"] == "synthesizers"

    @respx.mock
    def test_get_conditions(self, client):
        conditions = [{"uuid": "xyz", "display_name": "Mint", "slug": "mint"}]
        respx.get(f"{BASE_URL}/listings/conditions").mock(
            return_value=httpx.Response(200, json={"conditions": conditions})
        )
        result = client.get_conditions()
        assert len(result) == 1
        assert result[0]["display_name"] == "Mint"


class TestCaching:
    def test_save_and_load_categories(self, tmp_path):
        categories = [{"uuid": "abc", "slug": "synths"}]
        conditions = [{"uuid": "xyz", "slug": "mint"}]
        save_reference_data(categories, conditions, tmp_path)

        loaded_cats = load_cached_categories(tmp_path)
        assert loaded_cats == categories

        loaded_conds = load_cached_conditions(tmp_path)
        assert loaded_conds == conditions

    def test_load_returns_none_when_not_cached(self, tmp_path):
        assert load_cached_categories(tmp_path) is None
        assert load_cached_conditions(tmp_path) is None


# --- Context manager ---


class TestContextManager:
    def test_context_manager(self):
        with ReverbClient(token=FAKE_TOKEN) as client:
            assert client.token == FAKE_TOKEN
