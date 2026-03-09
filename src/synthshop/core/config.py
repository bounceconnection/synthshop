"""Application settings loaded from environment variables / .env file."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for SynthShop.

    Values are loaded from environment variables, with .env file as fallback.
    Only the keys needed for the current phase need to be set — optional fields
    default to None so the app can start without every service configured.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Anthropic (Claude Vision)
    anthropic_api_key: str | None = None

    # Reverb API
    reverb_api_token: str | None = None
    reverb_base_url: str = "https://api.reverb.com/api"

    # Cloudflare R2
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket_name: str = "synthshop"
    r2_public_url: str | None = None

    # Stripe
    stripe_secret_key: str | None = None
    stripe_publishable_key: str | None = None

    # Shop
    shop_base_url: str = "https://shop.bounceconnectionrecords.com"

    # Paths
    products_dir: Path = Path("products")

    def require_anthropic(self) -> str:
        """Return the Anthropic API key or raise if not configured."""
        if not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required. Set it in .env or environment.")
        return self.anthropic_api_key

    def require_reverb(self) -> str:
        """Return the Reverb API token or raise if not configured."""
        if not self.reverb_api_token:
            raise ValueError("REVERB_API_TOKEN is required. Set it in .env or environment.")
        return self.reverb_api_token

    def require_r2(self) -> tuple[str, str, str, str, str]:
        """Return R2 credentials or raise if not configured."""
        missing = [
            name
            for name, val in [
                ("R2_ACCOUNT_ID", self.r2_account_id),
                ("R2_ACCESS_KEY_ID", self.r2_access_key_id),
                ("R2_SECRET_ACCESS_KEY", self.r2_secret_access_key),
                ("R2_PUBLIC_URL", self.r2_public_url),
            ]
            if not val
        ]
        if missing:
            raise ValueError(f"R2 config incomplete. Missing: {', '.join(missing)}")
        return (
            self.r2_account_id,  # type: ignore[return-value]
            self.r2_access_key_id,  # type: ignore[return-value]
            self.r2_secret_access_key,  # type: ignore[return-value]
            self.r2_bucket_name,
            self.r2_public_url,  # type: ignore[return-value]
        )

    def require_stripe(self) -> str:
        """Return the Stripe secret key or raise if not configured."""
        if not self.stripe_secret_key:
            raise ValueError("STRIPE_SECRET_KEY is required. Set it in .env or environment.")
        return self.stripe_secret_key


# Singleton — import this from other modules
settings = Settings()
