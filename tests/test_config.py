"""Protected backend configuration and target binding."""

import pytest
from pydantic import ValidationError

from synthshop.core.config import Settings


def test_credentials_never_serialize_and_rotation_invalidates_binding():
    first = Settings(_env_file=None, reverb_api_token="private-one")
    second = Settings(_env_file=None, reverb_api_token="private-two")
    assert "private-one" not in first.model_dump_json()
    assert "private-one" not in repr(first)
    assert first.binding() != second.binding()


def test_no_credential_bearing_arbitrary_host():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, reverb_base_url="https://attacker.invalid/api")


def test_readiness_does_not_claim_scope_verification():
    settings = Settings(_env_file=None)
    assert not any(settings.readiness().values())
    with pytest.raises(ValueError):
        settings.require_reverb()
    with pytest.raises(ValueError):
        settings.require_r2()


def test_binding_covers_publishing_destination_and_staging_only():
    def binding(**changes):
        return Settings(_env_file=None, reverb_api_token="test-only", **changes).binding()

    assert binding() == binding(
        vision_model="other-model",
        openai_api_key="test-only",
        data_dir="/elsewhere",
        products_dir="/legacy",
        reverb_processed_photo_review_confirmed=True,
    )
    for change in (
        {"r2_bucket_name": "other"},
        {"r2_secret_access_key": "other"},
        {"expected_shop_slug": "other"},
        {"reverb_base_url": "https://sandbox.reverb.com/api"},
    ):
        assert binding() != binding(**change)


def test_production_writes_need_new_readiness_confirmation(monkeypatch):
    monkeypatch.setenv("REVERB_EXACT_PHOTOS_CONFIRMED", "true")
    settings = Settings(_env_file=None, reverb_exact_photos_confirmed=True)
    with pytest.raises(ValueError):
        settings.require_processed_review_writes()
    Settings(
        _env_file=None, reverb_processed_photo_review_confirmed=True
    ).require_processed_review_writes()
    Settings(
        _env_file=None, reverb_base_url="https://sandbox.reverb.com/api"
    ).require_processed_review_writes()
