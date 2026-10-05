"""Explicit approval -> one durable create -> verified photos -> publish -> reconcile."""

import hashlib
import html
import json
import re
import secrets
from decimal import Decimal, InvalidOperation

import httpx
from botocore.exceptions import BotoCoreError, ClientError

from synthshop.core.application import Application
from synthshop.core.models import Attempt, Draft
from synthshop.core.product_store import DraftConflictError
from synthshop.integrations.reverb import NOT_CREATED, ReverbAPIError, ReverbClient
from synthshop.integrations.staging import PhotoStaging


def text_content(value: str) -> str:
    """Compare provider HTML normalization without accepting changed public words."""
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", value)).split())


def remote_amount(value) -> Decimal:
    """Malformed provider money is an unverified outcome, not a crash."""
    try:
        amount = Decimal(str(value))
        if amount.is_finite():
            return amount
    except InvalidOperation:
        pass
    raise ReverbAPIError("Remote listing amount is malformed; publication not verified.")


class Publisher:
    """The only shipped remote-write workflow; HTTP GETs cannot call publish."""

    def __init__(self, application: Application):
        self.app = application

    def payload(self, draft: Draft, references: dict) -> dict:
        """Resolve identifiers and explicit rates before the final review screen."""
        condition = next(
            (row for row in references["conditions"] if row["display_name"] == draft.condition),
            None,
        )
        category = next(
            (
                row
                for row in references["categories"]
                if row["uuid"] == draft.category_id and row.get("listable", True)
            ),
            None,
        )
        if not condition or not category:
            raise ValueError("Select an allowed Reverb condition and category from reference data.")
        regions = {row["code"]: row["name"] for row in references["regions"]}
        if regions.get("US_CON") != "Continental U.S.":
            raise ValueError("Reverb continental-US region could not be confirmed.")
        if (
            not draft.shipping
            or draft.shipping[0].region_code != "US_CON"
            or draft.shipping[0].amount
        ):
            raise ValueError("Explicit free continental-US shipping is required.")
        if any(rate.region_code not in regions for rate in draft.shipping):
            raise ValueError("Unknown shipping destination; select a Reverb region code.")
        for name in ("make", "model", "title", "description", "condition", "price_reason"):
            if not getattr(draft, name).strip():
                raise ValueError(f"Complete {name.replace('_', ' ')} before final approval.")
        if draft.price is None or draft.price <= 0:
            raise ValueError("Enter a positive owner-reviewed USD asking price.")
        if draft.legacy_remote_id or draft.legacy_status not in (None, "draft"):
            raise ValueError(
                "Imported linked/non-draft product cannot become a fresh create. "
                "Manage its existing listing on Reverb."
            )
        if self.app.import_errors:
            raise ValueError(
                "Resolve failed legacy imports before publishing to prevent duplicates."
            )
        self.app.photos.verify(draft.photos)
        return {
            "make": draft.make,
            "model": draft.model,
            "title": draft.title,
            "description": "<p>" + html.escape(draft.description).replace("\n", "<br>") + "</p>",
            "condition": {"uuid": condition["uuid"]},
            "categories": [{"uuid": category["uuid"]}],
            "price": {"amount": str(draft.price), "currency": draft.currency},
            "offers_enabled": draft.offers_enabled,
            "has_inventory": False,
            "shipping": {
                "local": False,
                "rates": [
                    {
                        "region_code": rate.region_code,
                        "rate": {"amount": str(rate.amount), "currency": "USD"},
                    }
                    for rate in draft.shipping
                ],
            },
            "sku": f"synthshop-{draft.id}",
        }

    def fingerprint(self, draft: Draft, payload: dict) -> str:
        """Revision, complete fields/evidence, photo hashes/order and backend binding."""
        body = (
            draft.model_dump_json()
            + json.dumps(payload, sort_keys=True)
            + self.app.settings.binding()
        )
        return hashlib.sha256(body.encode()).hexdigest()

    def review(self, draft_id: str, revision: int) -> dict:
        """Read-only provider preflight; no staging/upload or listing creation."""
        draft = self.app.current(draft_id, revision)
        settings = self.app.settings
        with ReverbClient(settings, authenticated=bool(settings.reverb_api_token)) as client:
            references = client.references()
            payload = self.payload(draft, references)
            shop = client.verify_shop() if settings.reverb_api_token else None
        token = secrets.token_urlsafe(32)
        fingerprint = self.fingerprint(draft, payload)
        self.app.store.review(draft, token, fingerprint, payload)
        return {
            "draft": draft,
            "payload": payload,
            "token": token,
            "shop": shop,
            "references": references,
            "ready": all(settings.readiness().get(key) for key in ("reverb", "image_staging")),
        }

    def publish(self, draft_id: str, revision: int, token: str) -> Attempt:
        """Single durable POST opportunity. Repeated clicks only resume/reconcile that attempt."""
        with self.app.store.publish_lock():
            draft = self.app.current(draft_id, revision)
            settings = self.app.settings
            settings.require_reverb()
            settings.require_r2()
            # Production is specifically bound to bounceconnection. Sandbox can have its own ID.
            if "sandbox" not in settings.reverb_base_url and (
                settings.expected_shop_id != "1333667"
                or settings.expected_shop_slug != "bounceconnection"
            ):
                raise ValueError("Production target must be bounceconnection (1333667).")
            with ReverbClient(settings, authenticated=True) as client:
                client.verify_shop()
                payload = self.payload(draft, client.references())
                attempt = self.app.store.claim(draft, token, self.fingerprint(draft, payload))
                try:
                    return self._advance(client, draft, payload, attempt)
                except (
                    httpx.HTTPError,
                    ValueError,
                    KeyError,
                    OSError,
                    BotoCoreError,
                    ClientError,
                ) as exc:
                    if attempt.state == "prepared":
                        self.app.store.release(attempt)
                        detail = str(exc) if type(exc) in (ValueError, ReverbAPIError) else ""
                        raise ValueError(
                            f"No listing was created. {detail} Correct the configuration or "
                            "fields, then review and approve again."
                        ) from exc
                    # Remote errors can contain signed URLs or account data; never persist them.
                    if attempt.state == "published":
                        attempt.state = "published_unverified"
                    attempt.url = None
                    attempt.error = (
                        "Provider operation not verified. No second create will be sent after an "
                        "uncertain response. Reconcile this attempt; check permissions, photos, "
                        "account eligibility and remote listing on Reverb."
                    )
                    self.app.store.save_attempt(attempt)
                    raise ValueError(attempt.error) from exc

    def _advance(
        self, client: ReverbClient, draft: Draft, payload: dict, attempt: Attempt
    ) -> Attempt:
        """Recoverable state machine. A crash at creating never reopens the POST opportunity."""
        store = self.app.store
        if attempt.state == "prepared":
            self.app.settings.require_exact_photo_creates()
            staging = PhotoStaging(self.app.settings)
            urls = staging.stage(draft, attempt, store, self.app.photos)
            attempt.state = "creating"
            store.save_attempt(attempt)
            try:
                created = client.create_draft({**payload, "photos": urls})
            except ReverbAPIError as exc:
                if exc.status in NOT_CREATED:
                    attempt.state = "prepared"
                raise
            if not created.get("id"):
                raise ReverbAPIError("Create returned no durable ID; outcome unknown.")
            attempt.remote_id = str(created["id"])
            attempt.state = "remote"
            store.save_attempt(attempt)
        if not attempt.remote_id:
            matches = client.own_listings(attempt.correlation)
            if len(matches) != 1:
                raise ReverbAPIError(
                    "Create outcome unknown, even if lookup is empty. "
                    "Do not create another listing."
                )
            attempt.remote_id = str(matches[0]["id"])
            attempt.state = "remote"
            store.save_attempt(attempt)
        return self._complete(client, draft, payload, attempt)

    def _complete(
        self, client: ReverbClient, draft: Draft, payload: dict, attempt: Attempt
    ) -> Attempt:
        """Read back ownership/content/media before and after the live transition."""
        store = self.app.store
        remote = client.get_listing(attempt.remote_id)
        client.verify_ownership(remote, attempt.correlation)
        state = remote.get("state", {}).get("slug")
        self._verify_fields(remote, payload)
        self._verify_photos(client, remote, draft, attempt)
        store.save_attempt(attempt)
        if state == "draft":
            if attempt.state in ("published", "published_unverified"):
                raise DraftConflictError(
                    "Previously published listing is no longer live; no automatic relist."
                )
            attempt.state = "publishing"
            store.save_attempt(attempt)
            # All approved fields and ingested images were read back before making it live.
            client.update(attempt.remote_id, {**payload, "publish": True})
            remote = client.get_listing(attempt.remote_id)
            client.verify_ownership(remote, attempt.correlation)
            self._verify_fields(remote, payload)
            self._verify_photos(client, remote, draft, attempt)
        elif state != "live":
            raise ReverbAPIError(
                "Remote state is neither draft nor live; no automatic end/delete/relist."
            )
        if remote.get("state", {}).get("slug") != "live":
            raise ReverbAPIError("Remote listing is not confirmed live; reconcile later.")
        attempt.url = client.public_url(remote)
        attempt.state = "published"
        attempt.error = ""
        store.save_attempt(attempt)
        try:
            PhotoStaging(self.app.settings).cleanup(attempt, store)
        except (BotoCoreError, ClientError):
            attempt.error = (
                "Listing verified live; temporary image cleanup pending. Retry reconciliation."
            )
            store.save_attempt(attempt)
        return attempt

    @staticmethod
    def _verify_photos(client: ReverbClient, remote: dict, draft: Draft, attempt: Attempt) -> None:
        """Establish the ordered derivative binding, including cover, before adopting IDs."""
        image_ids, digests = client.photo_evidence(remote)
        approved = [photo.digest for photo in draft.photos]
        if digests != approved:
            raise ReverbAPIError(
                "Remote photo bytes/order do not exactly match approved derivatives. "
                "Transformed or unsupported evidence cannot authorize publication."
            )
        if attempt.image_digests:
            if attempt.image_ids != image_ids:
                raise ReverbAPIError(
                    "Remote photo identity/order changed; publication not verified."
                )
            if attempt.image_digests != approved:
                raise ReverbAPIError("Persisted photo binding differs from approval; not verified.")
        # Legacy ID-only baselines reach here only after exact byte/cover verification.
        attempt.image_ids = image_ids
        attempt.image_digests = digests

    @staticmethod
    def _verify_fields(remote: dict, payload: dict) -> None:
        """A live flag alone is not success; confirm the owner-approved outgoing listing."""
        for name in ("make", "model", "title", "offers_enabled"):
            if remote.get(name) != payload[name]:
                raise ReverbAPIError("Remote listing fields differ from approved revision.")
        if text_content(remote.get("description", "")) != text_content(payload["description"]):
            raise ReverbAPIError("Remote description differs from approved copy.")
        if remote.get("condition", {}).get("uuid") != payload["condition"]["uuid"]:
            raise ReverbAPIError("Remote condition differs from owner selection.")
        if {row["uuid"] for row in remote.get("categories", [])} != {
            row["uuid"] for row in payload["categories"]
        }:
            raise ReverbAPIError("Remote category differs from approval.")
        price = remote.get("price", {})
        if price.get("currency") != "USD" or remote_amount(price.get("amount")) != Decimal(
            payload["price"]["amount"]
        ):
            raise ReverbAPIError("Remote price/currency differs from approval.")

        def rates(shipping: dict) -> list[tuple]:
            return sorted(
                (row["region_code"], remote_amount(row["rate"]["amount"]), row["rate"]["currency"])
                for row in shipping.get("rates", [])
            )

        if rates(remote.get("shipping", {})) != rates(payload["shipping"]):
            raise ReverbAPIError("Remote shipping differs from approved rates.")
        if remote.get("shipping", {}).get("local") is not False:
            raise ReverbAPIError("Remote local-pickup setting differs from approval.")
