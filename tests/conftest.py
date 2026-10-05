"""No test reads credentials, existing inventory, or real provider accounts."""

import io

import pytest
from PIL import Image

from synthshop.core.application import Application
from synthshop.core.config import Settings


@pytest.fixture(autouse=True)
def empty_credentials(monkeypatch):
    for name in (
        "ANTHROPIC_API_KEY",
        "REVERB_API_TOKEN",
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "DATA_DIR",
        "PRODUCTS_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


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
