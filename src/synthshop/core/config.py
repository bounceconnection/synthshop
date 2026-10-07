"""Backend-only configuration. Secret values are never part of drafts or templates."""

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class MissingOpenAIKeyError(ValueError):
    """Vision was requested without a backend OpenAI key; guided setup applies."""


class Settings(BaseSettings):
    """Local single-owner settings, supplied by environment or protected .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", validate_assignment=True)
    openai_api_key: SecretStr | None = None
    vision_model: str = "gpt-4.1-mini"
    reverb_api_token: SecretStr | None = None
    reverb_base_url: Literal["https://api.reverb.com/api", "https://sandbox.reverb.com/api"] = (
        "https://api.reverb.com/api"
    )
    expected_shop_id: str = "1333667"
    expected_shop_slug: str = "bounceconnection"
    reverb_exact_photos_confirmed: bool = False
    data_dir: Path = Path.home() / ".synthshop"
    products_dir: Path = Path("products")
    r2_account_id: str | None = None
    r2_access_key_id: SecretStr | None = None
    r2_secret_access_key: SecretStr | None = None
    r2_bucket_name: str = "synthshop"

    def require_openai(self) -> str:
        """Fail without exposing a credential."""
        if not self.openai_api_key:
            raise MissingOpenAIKeyError(
                "OpenAI key is not set. Open Set up OpenAI in SynthShop for guided steps, "
                "or run synthshop setup-openai in your launch terminal."
            )
        return self.openai_api_key.get_secret_value()

    def require_reverb(self) -> str:
        """Publication needs profile/listing read and write_listings access."""
        if not self.reverb_api_token:
            raise ValueError("Configure REVERB_API_TOKEN before review approval/publishing.")
        return self.reverb_api_token.get_secret_value()

    def require_exact_photo_creates(self) -> None:
        """New production drafts need operator-confirmed sandbox exact-photo/cover evidence.

        An operator assertion, not proof: each listing still needs exact bytes, order and cover.
        """
        if "sandbox" not in self.reverb_base_url and not self.reverb_exact_photos_confirmed:
            raise ValueError(
                "New production Reverb drafts are disabled. Set REVERB_EXACT_PHOTOS_CONFIRMED "
                "only after separately authorized sandbox evidence shows exact full-photo and "
                "cover bytes. Existing attempts can still be reconciled."
            )

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
        """Invalidate review on token, destination, environment, or staging change only."""
        values = [
            self.reverb_base_url,
            self.expected_shop_id,
            self.expected_shop_slug,
            str(self.r2_account_id),
            self.r2_bucket_name,
        ]
        for secret in (self.reverb_api_token, self.r2_access_key_id, self.r2_secret_access_key):
            values.append(secret.get_secret_value() if secret else "")
        return hashlib.sha256("|".join(values).encode()).hexdigest()

    def readiness(self) -> dict[str, bool]:
        """Presence only, not authenticated compatibility or scope proof."""
        return {
            "vision": bool(self.openai_api_key),
            "reverb": bool(self.reverb_api_token),
            "image_staging": all(
                [self.r2_account_id, self.r2_access_key_id, self.r2_secret_access_key]
            ),
        }
