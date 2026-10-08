"""Two explicit grants: prepare one draft, review processed bytes, publish at most once."""

import hashlib
import html
import json
import re
import secrets
from decimal import Decimal, InvalidOperation

import httpx
from botocore.exceptions import BotoCoreError, ClientError

from synthshop.core.application import Application
from synthshop.core.models import (
    CONTRACT,
    Attempt,
    Draft,
    LocalPhotoBinding,
    PreparationGrant,
    ProcessedSnapshot,
    now,
)
from synthshop.core.product_store import DraftConflictError
from synthshop.core.validation import FIELD_LABELS, DraftFieldError
from synthshop.integrations.reverb import NOT_CREATED, ReverbAPIError, ReverbClient
from synthshop.integrations.staging import PhotoStaging

FAILURES = (
    httpx.HTTPError,
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    IndexError,
    OSError,
    BotoCoreError,
    ClientError,
)

STALE_RECOMMENDATION = {
    "price": "Kept as entered, but this was the recommendation for the previous maker, model, "
    "variant or condition and no longer applies. Choose Save owner corrections & copy first, "
    "then enter your own price (the same amount is fine) or research again.",
    "price_reason": "Kept as entered, but this reasoning belonged to that recommendation. "
    "After saving, enter your own reasoning.",
}


def with_reason(outcome: str, exc: Exception) -> str:
    """Append only this application's own sanitized refusal text, never library/provider bodies."""
    own = (ValueError, ReverbAPIError, DraftConflictError, DraftFieldError)
    detail = str(exc) if type(exc) in own else ""
    return f"{outcome} {detail}".strip()


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

    @staticmethod
    def field_errors(draft: Draft, references: dict) -> dict[str, str]:
        """Authoritative editable requirements, shared by review and publication."""
        errors = {}
        for name in ("make", "model", "title", "description", "price_reason"):
            if not getattr(draft, name).strip():
                errors[name] = f"Enter {FIELD_LABELS[name].lower()} before review."
        if not any(row["display_name"] == draft.condition for row in references["conditions"]):
            errors["condition"] = "Select an allowed Reverb condition."
        if not any(
            row["uuid"] == draft.category_id and row.get("listable", True)
            for row in references["categories"]
        ):
            errors["category_id"] = "Select an allowed Reverb category."
        if draft.price is None or draft.price <= 0:
            errors["price"] = "Enter a positive owner-reviewed USD asking price."
        regions = {row["code"] for row in references["regions"]}
        if any(rate.region_code not in regions for rate in draft.shipping):
            errors["international_rates"] = (
                "Unknown shipping destination; use a Reverb region code."
            )
        return errors

    @staticmethod
    def stale_recommendation(saved: Draft, draft: Draft, fields: dict) -> dict[str, str]:
        """Submitted unchanged recommended pricing that the candidate's edits invalidated."""
        return {
            name: message
            for name, message in STALE_RECOMMENDATION.items()
            if name not in draft.owner_fields
            and str(fields.get(name, "")).strip()
            and getattr(draft, name) != getattr(saved, name)
        }

    def payload(self, draft: Draft, references: dict) -> dict:
        """Resolve identifiers and explicit rates before the preparation review screen."""
        errors = self.field_errors(draft, references)
        if errors:
            raise DraftFieldError(errors, references)
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
        regions = {row["code"]: row["name"] for row in references["regions"]}
        if regions.get("US_CON") != "Continental U.S.":
            raise ValueError("Reverb continental-US region could not be confirmed.")
        if (
            not draft.shipping
            or draft.shipping[0].region_code != "US_CON"
            or draft.shipping[0].amount
        ):
            raise ValueError("Explicit free continental-US shipping is required.")
        if draft.legacy_remote_id or draft.legacy_status not in (None, "draft"):
            raise ValueError(
                "Imported linked/non-draft product cannot become a fresh create. "
                "Manage its existing listing on Reverb."
            )
        self._local_sources(draft)
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

    def _local_sources(self, draft: Draft) -> None:
        """Duplicate-import and derivative-tamper refusals need no provider data."""
        if self.app.import_errors:
            raise ValueError(
                "Resolve failed legacy imports before publishing to prevent duplicates."
            )
        self.app.photos.verify(draft.photos)

    def fingerprint(self, draft: Draft, payload: dict) -> str:
        """Revision, complete fields/evidence, photo hashes/order and backend binding."""
        body = (
            draft.model_dump_json()
            + json.dumps(payload, sort_keys=True)
            + self.app.settings.binding()
        )
        return hashlib.sha256(body.encode()).hexdigest()

    def _target(self, draft: Draft, shop: dict, *, historical: bool = False) -> PreparationGrant:
        settings = self.app.settings
        return PreparationGrant(
            authorized_at=None if historical else now(),
            historical=historical,
            api_origin=settings.reverb_base_url,
            shop_id=shop["id"],
            shop_slug=shop["slug"],
            backend_binding=settings.binding(),
            photos=[
                LocalPhotoBinding(id=p.id, digest=p.digest, position=i + 1)
                for i, p in enumerate(draft.photos)
            ],
        )

    def _destination(self) -> None:
        settings = self.app.settings
        settings.require_reverb()
        if "sandbox" not in settings.reverb_base_url and (
            settings.expected_shop_id != "1333667"
            or settings.expected_shop_slug != "bounceconnection"
        ):
            raise ValueError("Production target must be bounceconnection (1333667).")

    def review(self, draft_id: str, revision: int, fields: dict | None = None) -> dict:
        """Read-only on Reverb; submitted entries are saved only after every check passes.

        This preparation challenge cannot authorize a publish PUT.
        """
        self.app.store.attempt(draft_id)  # Release only an unsent reservation under its lock.
        with self.app.store.publish_lock():
            draft = self.app.current(draft_id, revision)
            if self.app.store.attempt(draft_id):
                raise DraftConflictError(
                    "Attempt exists. Inspect processed photos or check status."
                )
            errors = {}
            if fields is not None:
                saved = draft
                draft, errors = self.app.prepare_edit(saved.model_copy(deep=True), fields)
                errors = {**self.stale_recommendation(saved, draft, fields), **errors}
            settings = self.app.settings
            with ReverbClient(settings, authenticated=bool(settings.reverb_api_token)) as client:
                references = client.references()
                errors = {**self.field_errors(draft, references), **errors}
                if errors:
                    raise DraftFieldError(errors, references)
                payload = self.payload(draft, references)
                shop = client.verify_shop() if settings.reverb_api_token else None
            if fields is not None:
                draft = self.app.save(draft, revision)
            token = secrets.token_urlsafe(32)
            self.app.store.review(
                draft,
                token,
                self.fingerprint(draft, payload),
                payload,
            )
            return {
                "draft": draft,
                "payload": payload,
                "token": token,
                "shop": shop,
                "references": references,
                "ready": all(settings.readiness().get(k) for k in ("reverb", "image_staging")),
            }

    def prepare(self, draft_id: str, revision: int, token: str) -> Attempt:
        """One preparation-scoped grant stops at processed review, never publication."""
        with self.app.store.publish_lock():
            draft = self.app.current(draft_id, revision)
            self._destination()
            self.app.settings.require_r2()
            self.app.settings.require_processed_review_writes()
            with ReverbClient(self.app.settings, authenticated=True) as client:
                shop = client.verify_shop()
                payload = self.payload(draft, client.references())
                attempt = self.app.store.claim(
                    draft,
                    token,
                    self.fingerprint(draft, payload),
                    self._target(draft, shop),
                )
                try:
                    urls = PhotoStaging(self.app.settings).stage(
                        draft,
                        attempt,
                        self.app.store,
                        self.app.photos,
                    )
                    attempt.state = "creating"
                    self.app.store.save_attempt(attempt)
                    try:
                        created = client.create_draft({**payload, "photos": urls})
                    except ReverbAPIError as exc:
                        if exc.status in NOT_CREATED:
                            attempt.state = "prepared"
                        raise
                    remote_id = str(created.get("id", ""))
                    if not remote_id.isdigit():
                        raise ReverbAPIError("Create returned no durable ID; outcome unknown.")
                    attempt.remote_id = remote_id
                    attempt.state = "remote"
                    self.app.store.save_attempt(attempt)
                    self._refresh(client, draft, attempt)
                    return attempt
                except FAILURES as exc:
                    if attempt.state == "prepared":
                        self.app.store.release(attempt)
                        raise ValueError(
                            with_reason(
                                "No listing was created; correct configuration or fields, "
                                "then review and authorize preparation again.",
                                exc,
                            )
                        ) from exc
                    self._failure(attempt, exc)
                    raise ValueError(attempt.error) from exc

    def _local(self, draft: Draft, attempt: Attempt, *, writes: bool) -> None:
        """Refuse local/configuration drift before provider reads, keeping its own message."""
        try:
            if writes:
                self.app.settings.require_processed_review_writes()
            self._destination()
            payload = attempt.approved_payload
            if (
                not payload
                or attempt.revision != draft.revision
                or self.fingerprint(draft, payload) != attempt.fingerprint
            ):
                raise DraftConflictError(
                    "Original payload/revision/backend binding cannot be verified. Restore the "
                    "original binding or use separately authorized recovery; no rebinding."
                )
            self._local_sources(draft)
        except (ValueError, OSError):
            self.app.store.invalidate_review(draft.id)
            if attempt.state == "review_ready":
                attempt.state = "remote"
                self.app.store.save_attempt(attempt)
            raise

    def _bound(self, client: ReverbClient, draft: Draft, attempt: Attempt) -> None:
        """Authenticated shop and stored preparation target; never silently rebind."""
        shop = client.verify_shop()
        target = self._target(draft, shop, historical=True)
        if attempt.preparation:
            bound = attempt.preparation.model_dump(exclude={"authorized_at", "historical"})
            if bound != target.model_dump(exclude={"authorized_at", "historical"}):
                raise DraftConflictError("Destination or local photo binding changed.")
        elif attempt.contract == "historical":
            # The old review payload and exact historical fingerprint were retained at cutover.
            attempt.preparation = target
            if attempt.state in ("creating", "remote"):
                attempt.contract = CONTRACT
            self.app.store.save_attempt(attempt)
        else:
            raise DraftConflictError("Preparation binding is missing.")

    def _observe(self, client: ReverbClient, attempt: Attempt) -> dict:
        remote = client.get_listing(attempt.remote_id)
        attempt.observed_at = now()
        attempt.observed_state = remote.get("state", {}).get("slug")
        attempt.live_observed = attempt.live_observed or attempt.observed_state == "live"
        self.app.store.save_attempt(attempt)
        if str(remote.get("id")) != attempt.remote_id or remote.get("sku") != attempt.correlation:
            raise ReverbAPIError("Remote listing identity differs from the reserved attempt.")
        client.verify_ownership(remote, attempt.correlation)
        self._verify_fields(remote, attempt.approved_payload)
        return remote

    def _capture(
        self,
        client: ReverbClient,
        draft: Draft,
        attempt: Attempt,
        *,
        cache: bool,
        expected_state: str,
    ) -> tuple[ProcessedSnapshot, dict]:
        """Sandwich entity reads with consistent metadata; not an atomic provider snapshot.

        Only a draft capture can offer or consume a first-publish approval, so only it also
        requires current references to reproduce the approved fields.
        """
        remote = self._observe(client, attempt)
        if attempt.observed_state != expected_state:
            raise ReverbAPIError("Unexpected remote state; no automatic publish or relist.")
        if (
            expected_state == "draft"
            and self.payload(draft, client.references()) != attempt.approved_payload
        ):
            raise DraftConflictError(
                "Current Reverb reference data no longer reproduces the approved fields."
            )
        sources = client.media_sources(remote)
        entities = client.photo_evidence(sources)
        if len(entities) != len(draft.photos) + 1:
            raise ReverbAPIError("Returned gallery is incomplete or contains additional photos.")
        after = self._observe(client, attempt)
        if client.media_sources(after) != sources or attempt.observed_state != expected_state:
            raise ReverbAPIError("Remote gallery/cover/state changed during capture.")
        snapshot = ProcessedSnapshot(
            draft_id=draft.id,
            revision=draft.revision,
            fingerprint=attempt.fingerprint,
            payload=attempt.approved_payload,
            remote_id=attempt.remote_id,
            target=attempt.preparation,
            observed_state=expected_state,
            gallery=[entry for entry, _ in entities[:-1]],
            cover=entities[-1][0],
        )
        snapshot.seal()
        if cache:
            try:
                for entry, content in entities:
                    self.app.snapshots.put(entry, content)
            except (ValueError, OSError):
                self.app.snapshots.remove(snapshot)
                raise
        return snapshot, after

    def _refresh(self, client: ReverbClient, draft: Draft, attempt: Attempt) -> None:
        """Only a user-triggered pre-intent capture replaces an unapproved snapshot."""
        self.app.store.invalidate_review(draft.id)
        if (
            attempt.publish_intent_at
            or attempt.live_observed
            or attempt.state == "historical_unverified"
        ):
            self._complete(client, draft, attempt)
            return
        snapshot, _ = self._capture(client, draft, attempt, cache=True, expected_state="draft")
        previous = attempt.snapshot
        attempt.snapshot = snapshot
        attempt.state = "review_ready"
        attempt.error = ""
        self.app.store.save_attempt(attempt)
        if previous:
            self.app.snapshots.remove(previous)

    def processed_review(self, draft_id: str, revision: int) -> dict:
        """Cached GET surface: no remote reads or writes, and no implicit seller approval."""
        with self.app.store.publish_lock():
            draft = self.app.current(draft_id, revision)
            attempt = self.app.store.attempt(draft_id)
            if not attempt or not attempt.snapshot:
                raise ValueError(
                    "Processed photos are not yet reviewable. Check status explicitly."
                )
            self.app.snapshots.verify(attempt.snapshot)
            token = None
            blocked = ""
            if (
                attempt.state == "review_ready"
                and not attempt.publish_intent_at
                and not attempt.live_observed
            ):
                if self.fingerprint(draft, attempt.approved_payload) != attempt.fingerprint:
                    self.app.store.invalidate_review(draft_id)
                    raise DraftConflictError("Configuration changed. Restore the original binding.")
                self.app.photos.verify(draft.photos)
                try:
                    self.app.settings.require_processed_review_writes()
                except ValueError as exc:
                    self.app.store.invalidate_review(draft_id)
                    blocked = str(exc)
                else:
                    token = secrets.token_urlsafe(32)
                    self.app.store.review(
                        draft,
                        token,
                        attempt.fingerprint,
                        attempt.approved_payload,
                        snapshot=attempt.snapshot,
                    )
            return {
                "draft": draft,
                "attempt": attempt,
                "snapshot": attempt.snapshot,
                "payload": attempt.approved_payload,
                "token": token,
                "blocked": blocked,
            }

    def decline(self, draft_id: str, revision: int) -> Attempt:
        """Retain remote draft, staging, SKU and revision lock; revoke only the challenge."""
        with self.app.store.publish_lock():
            self.app.current(draft_id, revision)
            attempt = self.app.store.attempt(draft_id)
            if not attempt:
                raise KeyError("Attempt not found")
            self.app.store.invalidate_review(draft_id)
            if attempt.state == "review_ready" and not attempt.live_observed:
                attempt.state = "remote"
                attempt.error = "Review declined. Remote draft retained; correction is manual."
                self.app.store.save_attempt(attempt)
            return attempt

    def publish(self, draft_id: str, revision: int, token: str) -> Attempt:
        """Fresh matching preflight plus final-scoped approval buys at most one PUT."""
        with self.app.store.publish_lock():
            draft = self.app.current(draft_id, revision)
            attempt = self.app.store.attempt(draft_id)
            if not attempt or attempt.state != "review_ready" or attempt.publish_intent_at:
                raise DraftConflictError("No unconsumed processed review. Use read-only status.")
            self._local(draft, attempt, writes=True)
            try:
                with ReverbClient(self.app.settings, authenticated=True) as client:
                    self._bound(client, draft, attempt)
                    self.app.snapshots.verify(attempt.snapshot)
                    current, _ = self._capture(
                        client,
                        draft,
                        attempt,
                        cache=False,
                        expected_state="draft",
                    )
                    if current.projection() != attempt.snapshot.projection():
                        raise DraftConflictError(
                            "Reviewed processed evidence changed; review again."
                        )
                    attempt = self.app.store.claim_publish(draft, token, attempt)
                    client.update(attempt.remote_id, {**attempt.approved_payload, "publish": True})
                    self._complete(client, draft, attempt)
                    return attempt
            except FAILURES as exc:
                self._failure(attempt, exc)
                raise ValueError(attempt.error) from exc

    def reconcile(self, draft_id: str, revision: int) -> Attempt:
        """Read-only Reverb recovery. Never stage, create, publish, relist or delete."""
        with self.app.store.publish_lock():
            draft = self.app.current(draft_id, revision)
            attempt = self.app.store.attempt(draft_id)
            if not attempt:
                raise KeyError("Attempt not found")
            self._local(draft, attempt, writes=False)
            try:
                with ReverbClient(self.app.settings, authenticated=True) as client:
                    self._bound(client, draft, attempt)
                    if not attempt.remote_id:
                        matches = client.own_listings(attempt.correlation)
                        if len(matches) != 1 or not str(matches[0].get("id", "")).isdigit():
                            raise ReverbAPIError(
                                "Exact-SKU lookup did not find exactly one owned listing."
                            )
                        attempt.remote_id = str(matches[0]["id"])
                        if attempt.state == "creating":
                            attempt.state = "remote"
                        self.app.store.save_attempt(attempt)
                    self._refresh(client, draft, attempt)
                    return attempt
            except FAILURES as exc:
                self._failure(attempt, exc)
                raise ValueError(attempt.error) from exc

    def _complete(self, client: ReverbClient, draft: Draft, attempt: Attempt) -> None:
        """Observe the original authorized baseline only; absolutely no Reverb writes."""
        if not attempt.final_approval or not attempt.publish_intent_at:
            self._observe(client, attempt)
            raise ReverbAPIError(
                "No prepublication processed approval; historical/live unverified."
            )
        self.app.snapshots.verify(attempt.snapshot)
        if (
            attempt.final_approval.snapshot_id != attempt.snapshot.id
            or attempt.final_approval.manifest_digest != attempt.snapshot.digest
        ):
            raise ReverbAPIError("Approved snapshot binding is invalid.")
        current, remote = self._capture(
            client,
            draft,
            attempt,
            cache=False,
            expected_state="live",
        )
        if current.projection() != attempt.snapshot.projection():
            raise ReverbAPIError("Live processed evidence differs from the reviewed snapshot.")
        attempt.url = client.public_url(remote)
        attempt.state = "published"
        attempt.error = ""
        self.app.store.save_attempt(attempt)
        try:
            PhotoStaging(self.app.settings).cleanup(attempt, self.app.store)
        except (BotoCoreError, ClientError, OSError, ValueError):
            attempt.error = (
                "Verified live; known staging cleanup pending. Check status to retry cleanup."
            )
            self.app.store.save_attempt(attempt)

    def _failure(self, attempt: Attempt, exc: Exception) -> None:
        """Clear success and record a sanitized reason; never promise a failed write was private."""
        self.app.store.invalidate_review(attempt.draft_id)
        attempt.url = None
        if attempt.state == "historical_unverified":
            outcome = "Historical write remains unverified; no retroactive approval or replay."
        elif attempt.publish_intent_at or attempt.live_observed:
            attempt.state = "published_unverified"
            outcome = (
                "Last observed live; reviewed evidence not verified. It may be publicly visible."
                if attempt.observed_state == "live"
                else "Publication outcome unknown; it may be live. No publish replay is allowed."
            )
        elif not attempt.remote_id:
            outcome = (
                "Create outcome unknown; a remote draft may exist. No second create will be sent "
                "after an uncertain response. Check status for a read-only exact-SKU lookup."
            )
        else:
            attempt.state = "remote"
            outcome = (
                "Remote draft/evidence not reviewable or approval invalidated. Check status for "
                "a fresh capture and review again. No second create; correction is manual."
            )
        attempt.error = with_reason(outcome, exc)
        self.app.store.save_attempt(attempt)

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
