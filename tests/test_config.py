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
