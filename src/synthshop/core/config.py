"""Backend-only configuration. Secret values are never part of drafts or templates."""

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Local single-owner settings, supplied by environment or protected .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", validate_assignment=True)
    anthropic_api_key: SecretStr | None = None
    vision_model: str = "claude-sonnet-4-6"
    reverb_api_token: SecretStr | None = None
    reverb_base_url: Literal["https://api.reverb.com/api", "https://sandbox.reverb.com/api"] = (
        "https://api.reverb.com/api"
    )
    expected_shop_id: str = "1333667"
    expected_shop_slug: str = "bounceconnection"
    data_dir: Path = Path.home() / ".synthshop"
    products_dir: Path = Path("products")
    r2_account_id: str | None = None
    r2_access_key_id: SecretStr | None = None
    r2_secret_access_key: SecretStr | None = None
    r2_bucket_name: str = "synthshop"

    def require_anthropic(self) -> str:
        """Fail without exposing a credential."""
        if not self.anthropic_api_key:
            raise ValueError("Configure ANTHROPIC_API_KEY in backend .env to analyze photos.")
        return self.anthropic_api_key.get_secret_value()

    def require_reverb(self) -> str:
        """Publication needs profile/listing read and write_listings access."""
        if not self.reverb_api_token:
            raise ValueError("Configure REVERB_API_TOKEN before review approval/publishing.")
        return self.reverb_api_token.get_secret_value()

    def require_r2(self) -> tuple[str, str, str, str]:
        """Private signed-URL staging; no public bucket needed."""
        if not all([self.r2_account_id, self.r2_access_key_id, self.r2_secret_access_key]):
            raise ValueError("Configure private R2 image staging before publishing.")
        return (
            str(self.r2_account_id),
            self.r2_access_key_id.get_secret_value(),
            self.r2_secret_access_key.get_secret_value(),
            self.r2_bucket_name,
        )

    def binding(self) -> str:
        """Invalidate review on token, destination, environment, or staging change."""
        values = [str(value) for value in self.model_dump(mode="json").values()]
        for secret in (self.reverb_api_token, self.r2_access_key_id, self.r2_secret_access_key):
            values.append(secret.get_secret_value() if secret else "")
        return hashlib.sha256("|".join(values).encode()).hexdigest()

    def readiness(self) -> dict[str, bool]:
        """Presence only, not authenticated compatibility or scope proof."""
        return {
            "vision": bool(self.anthropic_api_key),
            "reverb": bool(self.reverb_api_token),
            "image_staging": all(
                [self.r2_account_id, self.r2_access_key_id, self.r2_secret_access_key]
            ),
        }
