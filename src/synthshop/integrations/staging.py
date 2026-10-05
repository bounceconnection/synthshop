"""Temporary private R2 objects; signed GET URLs are sent only to Reverb."""

from urllib.parse import urlparse

import boto3
from botocore.config import Config

from synthshop.core.config import Settings
from synthshop.core.models import Attempt, Draft
from synthshop.core.photos import PhotoLibrary
from synthshop.core.product_store import DraftStore


class PhotoStaging:
    """Never provision buckets, public ACLs, tunnels, or upload before approval."""

    def __init__(self, settings: Settings):
        account, key, secret, self.bucket = settings.require_r2()
        if not account.isalnum():
            raise ValueError("Invalid R2 account identifier")
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            region_name="auto",
            config=Config(signature_version="s3v4", retries={"max_attempts": 0}),
        )

    def stage(
        self, draft: Draft, attempt: Attempt, store: DraftStore, library: PhotoLibrary
    ) -> list[str]:
        """Persist cleanup identities before the first upload; only approved derivatives."""
        self._verify_expiry()
        attempt.staged_keys = [
            f"synthshop/{attempt.correlation}/{photo.id}.jpg" for photo in draft.photos
        ]
        store.save_attempt(attempt)
        urls = []
        for photo, key in zip(draft.photos, attempt.staged_keys, strict=True):
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=library.path(photo.id).read_bytes(),
                ContentType="image/jpeg",
            )
            url = self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=3600,
            )
            if urlparse(url).scheme != "https":
                raise ValueError("Image staging must supply signed HTTPS URLs")
            urls.append(url)
        return urls

    def _verify_expiry(self) -> None:
        """Require an existing one-day expiry so failed attempts cannot leak objects forever."""
        rules = self.client.get_bucket_lifecycle_configuration(Bucket=self.bucket).get("Rules", [])
        for rule in rules:
            selector = rule.get("Filter", {})
            prefix = selector.get("Prefix", rule.get("Prefix", ""))
            days = rule.get("Expiration", {}).get("Days")
            if (
                rule.get("Status") == "Enabled"
                and set(selector).issubset({"Prefix"})
                and "synthshop/".startswith(prefix)
                and isinstance(days, int)
                and 0 < days <= 1
            ):
                return
        raise ValueError(
            "Image staging needs an existing enabled one-day expiry for the synthshop/ prefix."
        )

    def cleanup(self, attempt: Attempt, store: DraftStore) -> None:
        """Explicitly clean known objects; bucket lifecycle bounds leftovers after crashes."""
        for key in list(attempt.staged_keys):
            self.client.delete_object(Bucket=self.bucket, Key=key)
            attempt.staged_keys.remove(key)
            store.save_attempt(attempt)
