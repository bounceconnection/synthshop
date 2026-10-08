"""No test reads credentials, existing inventory, or real provider accounts."""

import io
import socket

import pytest
from PIL import Image

from synthshop.core.application import Application
from synthshop.core.config import Settings


@pytest.fixture(autouse=True)
def empty_credentials(monkeypatch):
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
        "VISION_MODEL",
        "REVERB_API_TOKEN",
        "REVERB_BASE_URL",
        "REVERB_PROCESSED_PHOTO_REVIEW_CONFIRMED",
        "REVERB_EXACT_PHOTOS_CONFIRMED",
        "EXPECTED_SHOP_ID",
        "EXPECTED_SHOP_SLUG",
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET_NAME",
        "DATA_DIR",
        "PRODUCTS_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    """A provider-double mismatch must fail locally, never contact a real account."""

    def reject_connection(*_args, **_kwargs):
        raise AssertionError("Tests cannot open network connections")

    monkeypatch.setattr(socket.socket, "connect", reject_connection)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_connection)


@pytest.fixture
def image_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (96, 64), "orange").save(stream, "WEBP")
    return stream.getvalue()


@pytest.fixture
def application(tmp_path):
    return Application(
        Settings(_env_file=None, data_dir=tmp_path / "data", products_dir=tmp_path / "legacy")
    )


@pytest.fixture
def draft(application, image_bytes):
    item = application.upload([image_bytes])
    item.make = "Example Instruments"
    item.model = "Meter Stereo"
    item.condition = "Poor"
    item.category_id = "utility-uuid"
    item.title = "Example Instruments Meter Stereo"
    item.description = "Untested. Scratched panel."
    item.price = "190.00"
    item.price_reason = "Owner price, insufficient market evidence"
    item.owner_fields = ["price", "price_reason"]
    return application.store.save(item, item.revision)


@pytest.fixture
def references():
    return {
        "conditions": [{"display_name": "Poor", "uuid": "poor-uuid"}],
        "categories": [
            {"uuid": "utility-uuid", "full_name": "Pro Audio / Utility", "listable": True}
        ],
        "regions": [
            {"code": "US_CON", "name": "Continental U.S."},
            {"code": "CA", "name": "Canada"},
        ],
    }
