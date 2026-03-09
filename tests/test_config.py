"""Tests for configuration loading."""

import pytest

from synthshop.core.config import Settings


class TestSettings:
    def test_defaults(self):
        """Settings can be created with all defaults (no env vars)."""
        s = Settings(
            _env_file=None,  # Don't read .env during tests
        )
        assert s.anthropic_api_key is None
        assert s.reverb_base_url == "https://api.reverb.com/api"
        assert s.r2_bucket_name == "synthshop"

    def test_require_anthropic_raises_when_missing(self):
        s = Settings(_env_file=None)
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            s.require_anthropic()

    def test_require_anthropic_returns_key(self):
        s = Settings(_env_file=None, anthropic_api_key="sk-ant-test")
        assert s.require_anthropic() == "sk-ant-test"

    def test_require_reverb_raises_when_missing(self):
        s = Settings(_env_file=None)
        with pytest.raises(ValueError, match="REVERB_API_TOKEN"):
            s.require_reverb()

    def test_require_r2_raises_with_missing_fields(self):
        s = Settings(_env_file=None, r2_account_id="acct123")
        with pytest.raises(ValueError, match="R2_ACCESS_KEY_ID"):
            s.require_r2()

    def test_require_r2_returns_tuple(self):
        s = Settings(
            _env_file=None,
            r2_account_id="acct",
            r2_access_key_id="key",
            r2_secret_access_key="secret",
            r2_public_url="https://cdn.example.com",
        )
        result = s.require_r2()
        assert result == ("acct", "key", "secret", "synthshop", "https://cdn.example.com")

    def test_require_stripe_raises_when_missing(self):
        s = Settings(_env_file=None)
        with pytest.raises(ValueError, match="STRIPE_SECRET_KEY"):
            s.require_stripe()
