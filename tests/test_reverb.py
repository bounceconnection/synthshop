"""Deterministic two-grant safety contracts, not real Reverb compatibility evidence."""

import hashlib
import io
import json
import re
from copy import deepcopy
from unittest.mock import patch

import httpx
import pytest
import respx
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from PIL import Image

from synthshop.core.application import Application
from synthshop.core.config import Settings
from synthshop.core.models import Attempt, ShippingRate
from synthshop.core.publishing import Publisher
from synthshop.integrations.reverb import IMAGE_HEADERS, ReverbAPIError, ReverbClient
from tests.test_cli import LOCAL, browser


def synthetic_image(color, size=(96, 64)):
    """Generated fixtures, never inventory or provider downloads."""
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, "PNG")
    return stream.getvalue()


class Provider(ReverbClient):
    """Offline stateful double using real selector, fetch, and public-URL validation."""

    def __init__(self, references, settings):
        self.settings = settings
        self.reference_data = references
        self.remote = None
        self.creates = 0
        self.updates = 0
        self.reject_create = None
        self.timeout_create = False
        self.timeout_publish = False
        self.empty_lookup = False
        self.wrong_shop = False
        self.initial_images = None
        self.cover_bytes = None
        self.image_bytes = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def references(self):
        return self.reference_data

    def verify_shop(self):
        if self.wrong_shop:
            raise ValueError("Wrong shop")
        return {"id": "1333667", "slug": "bounceconnection", "name": "Offline fixture shop"}

    def create_draft(self, payload):
        self.creates += 1
        if self.reject_create:
            raise ReverbAPIError("Reverb POST failed", self.reject_create)
        self.remote = deepcopy(payload)
        images = self.initial_images
        if images is None:
            images = [Staging.images[url] for url in payload["photos"]]
        self.image_bytes = {
            f"https://images.reverb.com/image/upload/photo-{index}.jpg?signature=secret-fixture": content
            for index, content in enumerate(images)
        }
        photos = [{"id": 101 + index, "url": url} for index, url in enumerate(self.image_bytes)]
        cover = next(iter(self.image_bytes))
        if self.cover_bytes:
            cover = "https://rvb-img.reverb.com/cover?signature=secret-cover"
            self.image_bytes[cover] = self.cover_bytes
        self.remote.update(
            id=42,
            shop_id=1333667,
            state={"slug": "draft"},
            photos=photos,
            publish=False,
            _links={
                "web": {"href": "https://reverb.com/item/42-example-meter"},
                "photo": {"href": cover},
            },
        )
        if self.timeout_create:
            raise httpx.ReadTimeout("private response lost")
        return deepcopy(self.remote)

    def own_listings(self, sku):
        return (
            []
            if self.empty_lookup or self.remote is None or self.remote["sku"] != sku
            else [deepcopy(self.remote)]
        )

    def get_listing(self, _listing_id):
        return deepcopy(self.remote)

    def update(self, _listing_id, payload):
        assert "photos" not in payload
        self.updates += 1
        self.remote.update(payload)
        self.remote["state"] = {"slug": "live"}
        if self.timeout_publish:
            raise httpx.ReadTimeout("private publish response lost")


class Staging:
    """Explicitly offline storage double, with observable stage/cleanup effects."""

    failure = None
    images = {}
    stages = 0
    cleanups = 0

    def __init__(self, _settings):
        pass

    def stage(self, draft, attempt, store, photos):
        Staging.stages += 1
        if Staging.failure:
            raise Staging.failure
        Staging.images = {
            f"https://example.invalid/{photo.id}.jpg": photos.path(photo.id).read_bytes()
            for photo in draft.photos
        }
        attempt.staged_keys = [f"synthshop/{attempt.correlation}/{p.id}.jpg" for p in draft.photos]
        store.save_attempt(attempt)
        return list(Staging.images)

    def cleanup(self, attempt, store):
        Staging.cleanups += 1
        attempt.staged_keys = []
        store.save_attempt(attempt)


@pytest.fixture
def publication(application, references):
    application.settings.reverb_api_token = "test-only"
    application.settings.r2_account_id = "example"
    application.settings.r2_access_key_id = "test-only"
    application.settings.r2_secret_access_key = "test-only"
    application.settings.reverb_processed_photo_review_confirmed = True
    provider = Provider(references, application.settings)
    Staging.failure = None
    Staging.stages = Staging.cleanups = 0
    with (
        patch("synthshop.core.publishing.ReverbClient", return_value=provider),
        patch("synthshop.core.publishing.PhotoStaging", Staging),
        respx.mock(assert_all_called=False) as network,
    ):

        def serve_image(request):
            assert all(
                name not in request.headers for name in ("authorization", "cookie", "referer")
            )
            assert all(request.headers[name] == value for name, value in IMAGE_HEADERS.items())
            content = provider.image_bytes.get(str(request.url))
            if content is None:
                return httpx.Response(404)
            with Image.open(io.BytesIO(content)) as image:
                mime = Image.MIME[image.format]
            return httpx.Response(200, content=content, headers={"Content-Type": mime})

        network.get(host="images.reverb.com").mock(side_effect=serve_image)
        network.get(host="rvb-img.reverb.com").mock(side_effect=serve_image)
        yield Publisher(application), provider


def prepare(publisher, draft):
    review = publisher.review(draft.id, draft.revision)
    return publisher.prepare(draft.id, draft.revision, review["token"])


def approve(publisher, draft):
    review = publisher.processed_review(draft.id, draft.revision)
    return publisher.publish(draft.id, draft.revision, review["token"])


def post(client, csrf, draft, action, **fields):
    return client.post(
        f"/drafts/{draft.id}/{action}",
        data={"csrf": csrf, "revision": draft.revision, **fields},
        headers={"Origin": LOCAL},
    )


def token(response):
    return re.search(r'name="token" value="([^"]+)"', response.text).group(1)


def test_prepare_stops_unpublished_and_tokens_cannot_cross_purposes(
    publication, application, draft
):
    publisher, provider = publication
    first = publisher.review(draft.id, draft.revision)
    assert provider.creates == Staging.stages == provider.updates == 0
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, first["token"])
    attempt = publisher.prepare(draft.id, draft.revision, first["token"])
    assert attempt.state == "review_ready"
    assert provider.remote["state"]["slug"] == "draft"
    assert provider.creates == Staging.stages == 1
    assert provider.updates == Staging.cleanups == 0
    final = publisher.processed_review(draft.id, draft.revision)
    with pytest.raises(ValueError):
        publisher.prepare(draft.id, draft.revision, final["token"])
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, first["token"])
    assert provider.updates == 0
    assert application.store.attempt(draft.id).publish_intent_at is None


def test_transformed_gallery_and_independent_cover_publish_only_after_review(
    publication, application, draft
):
    publisher, provider = publication
    draft = application.upload([synthetic_image("blue")], draft.id, draft.revision)
    provider.initial_images = [
        synthetic_image("orange", (70, 50)),
        synthetic_image("blue", (60, 40)),
    ]
    provider.cover_bytes = synthetic_image("orange", (40, 40))
    attempt = prepare(publisher, draft)
    snapshot = attempt.snapshot
    assert [entry.digest for entry in snapshot.gallery] != [photo.digest for photo in draft.photos]
    assert snapshot.cover.digest != snapshot.gallery[0].digest
    assert provider.updates == 0
    result = approve(publisher, draft)
    assert result.state == "published"
    assert result.url == "https://reverb.com/item/42-example-meter"
    assert result.snapshot == snapshot
    assert result.final_approval.manifest_digest == snapshot.digest
    assert provider.creates == provider.updates == 1
    assert Staging.cleanups == 1
    assert application.store.load(draft.id) == draft
    assert provider.remote["description"] == "<p>Untested. Scratched panel.</p>"
    assert "price_reason" not in provider.remote


def test_duplicate_clicks_and_restart_cannot_duplicate_create_or_publish(
    publication, application, draft
):
    publisher, provider = publication
    preparation = publisher.review(draft.id, draft.revision)
    publisher.prepare(draft.id, draft.revision, preparation["token"])
    restarted = Publisher(Application(application.settings))
    with pytest.raises(ValueError):
        restarted.prepare(draft.id, draft.revision, preparation["token"])
    review = restarted.processed_review(draft.id, draft.revision)
    restarted.publish(draft.id, draft.revision, review["token"])
    with pytest.raises(ValueError):
        restarted.publish(draft.id, draft.revision, review["token"])
    assert restarted.reconcile(draft.id, draft.revision).state == "published"
    assert provider.creates == provider.updates == Staging.stages == 1


@pytest.mark.parametrize(
    "change",
    [
        "order",
        "identity",
        "locator",
        "bytes",
        "cover",
        "missing",
        "added",
        "relation",
        "unavailable",
        "field",
        "shop",
        "sku",
        "local",
        "config",
        "reference",
    ],
)
def test_changed_evidence_prevents_first_put_and_revokes_token(
    publication, application, draft, change
):
    publisher, provider = publication
    draft = application.upload([synthetic_image("blue")], draft.id, draft.revision)
    prepare(publisher, draft)
    final = publisher.processed_review(draft.id, draft.revision)
    remote, images = deepcopy(provider.remote), dict(provider.image_bytes)
    photo = provider.remote["photos"][0]
    if change == "order":
        provider.remote["photos"].reverse()
    elif change == "identity":
        photo["id"] = 999
    elif change == "locator":
        old = photo["url"]
        photo["url"] += "&rotated=1"
        provider.image_bytes[photo["url"]] = provider.image_bytes[old]
    elif change == "bytes":
        provider.image_bytes[photo["url"]] = synthetic_image("green")
    elif change == "cover":
        provider.remote["_links"]["photo"]["href"] = provider.remote["photos"][1]["url"]
    elif change == "missing":
        provider.remote["photos"].pop()
    elif change == "added":
        provider.remote["photos"].append({"id": 999, "url": "https://images.reverb.com/add.jpg"})
        provider.image_bytes["https://images.reverb.com/add.jpg"] = synthetic_image("green")
    elif change == "relation":
        photo["_links"] = {"full": {"href": photo["url"]}}
    elif change == "unavailable":
        del provider.image_bytes[photo["url"]]
    elif change == "field":
        provider.remote["title"] = "Changed remotely"
    elif change == "shop":
        provider.remote["shop_id"] = 1
    elif change == "sku":
        provider.remote["sku"] = "another-draft"
    elif change == "local":
        application.photos.path(draft.photos[0].id).write_bytes(b"changed")
    elif change == "config":
        application.settings.r2_bucket_name = "different"
    else:
        provider.reference_data["conditions"][0]["uuid"] = "changed"
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, final["token"])
    saved = application.store.attempt(draft.id)
    assert saved.state == "remote"
    assert saved.publish_intent_at is None
    assert saved.snapshot == final["snapshot"]
    provider.remote, provider.image_bytes = remote, images
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, final["token"])
    assert provider.updates == 0


def test_refresh_invalidates_old_final_approval_and_replaces_only_unapproved_blobs(
    publication, application, draft
):
    publisher, provider = publication
    original = prepare(publisher, draft).snapshot
    old = publisher.processed_review(draft.id, draft.revision)
    new = publisher.reconcile(draft.id, draft.revision).snapshot
    assert original.id != new.id
    assert original.projection() == new.projection()
    assert not application.snapshots.path(original.cover.blob_id).exists()
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, old["token"])
    assert provider.updates == 0
    publisher.reconcile(draft.id, draft.revision)
    assert approve(publisher, draft).state == "published"


@pytest.mark.parametrize("outcome", ["draft", "unknown", "live"])
def test_intent_before_request_crash_is_never_replayed(publication, application, draft, outcome):
    publisher, provider = publication
    prepare(publisher, draft)
    final = publisher.processed_review(draft.id, draft.revision)
    with patch.object(provider, "update", side_effect=SystemExit), pytest.raises(SystemExit):
        publisher.publish(draft.id, draft.revision, final["token"])
    restarted = Publisher(Application(application.settings))
    saved = application.store.attempt(draft.id)
    assert saved.state == "publishing"
    assert saved.publish_intent_at and saved.final_approval
    assert provider.updates == 0
    if outcome == "live":
        provider.remote["state"]["slug"] = "live"
        assert restarted.reconcile(draft.id, draft.revision).state == "published"
    else:
        if outcome == "unknown":
            provider.remote["state"]["slug"] = "unknown"
        with pytest.raises(ValueError):
            restarted.reconcile(draft.id, draft.revision)
        saved = application.store.attempt(draft.id)
        assert saved.state == "published_unverified"
        assert saved.url is None
    assert provider.updates == 0
    assert application.store.attempt(draft.id).snapshot == final["snapshot"]


def test_timeout_then_changed_live_evidence_preserves_original_approval(
    publication, application, draft
):
    publisher, provider = publication
    prepare(publisher, draft)
    final = publisher.processed_review(draft.id, draft.revision)
    provider.timeout_publish = True
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, final["token"])
    photo_url = provider.remote["photos"][0]["url"]
    original = provider.image_bytes[photo_url]
    provider.image_bytes[photo_url] = synthetic_image("green")
    with pytest.raises(ValueError):
        publisher.reconcile(draft.id, draft.revision)
    saved = application.store.attempt(draft.id)
    assert saved.live_observed and saved.state == "published_unverified" and saved.url is None
    assert saved.snapshot == final["snapshot"]
    provider.image_bytes[photo_url] = original
    result = publisher.reconcile(draft.id, draft.revision)
    assert result.state == "published" and result.snapshot == final["snapshot"]
    provider.remote["state"]["slug"] = "draft"
    with pytest.raises(ValueError):
        publisher.reconcile(draft.id, draft.revision)
    assert application.store.attempt(draft.id).url is None
    assert provider.creates == provider.updates == 1


def test_post_put_drift_is_live_unverified_not_new_baseline(publication, application, draft):
    publisher, provider = publication
    snapshot = prepare(publisher, draft).snapshot
    update = provider.update

    def mutate(listing_id, payload):
        update(listing_id, payload)
        provider.image_bytes[provider.remote["_links"]["photo"]["href"]] = synthetic_image("green")

    with patch.object(provider, "update", side_effect=mutate), pytest.raises(ValueError):
        approve(publisher, draft)
    saved = application.store.attempt(draft.id)
    assert saved.state == "published_unverified" and saved.live_observed
    assert saved.url is None and saved.snapshot == snapshot
    assert provider.updates == 1 and Staging.cleanups == 0


def test_unexpected_live_without_final_approval_can_never_be_certified(
    publication, application, draft
):
    publisher, provider = publication
    prepare(publisher, draft)
    provider.remote["state"]["slug"] = "live"
    with pytest.raises(ValueError):
        publisher.reconcile(draft.id, draft.revision)
    provider.remote["state"]["slug"] = "draft"
    with pytest.raises(ValueError):
        publisher.reconcile(draft.id, draft.revision)
    saved = application.store.attempt(draft.id)
    assert saved.live_observed and not saved.final_approval and saved.url is None
    assert publisher.processed_review(draft.id, draft.revision)["token"] is None
    assert provider.updates == 0


def test_decline_and_staging_expiry_keep_remote_and_lock_without_cleanup(
    publication, application, draft
):
    publisher, provider = publication
    snapshot = prepare(publisher, draft).snapshot
    final = publisher.processed_review(draft.id, draft.revision)
    publisher.decline(draft.id, draft.revision)
    Staging.images = {}  # Source handoff has expired; cached evidence is independent.
    restarted = Publisher(Application(application.settings))
    assert restarted.processed_review(draft.id, draft.revision)["snapshot"] == snapshot
    with pytest.raises(ValueError):
        restarted.publish(draft.id, draft.revision, final["token"])
    with pytest.raises(ValueError):
        application.edit(draft.id, draft.revision, {"title": "replacement"})
    assert provider.creates == 1 and provider.updates == Staging.cleanups == 0
    assert application.store.attempt(draft.id).remote_id == "42"
    assert restarted.reconcile(draft.id, draft.revision).state == "review_ready"
    assert approve(restarted, draft).state == "published"
    assert Staging.stages == 1


def test_ambiguous_create_empty_lookup_then_discovery_never_reposts(
    publication, application, draft
):
    publisher, provider = publication
    provider.timeout_create = True
    with pytest.raises(ValueError, match="Create outcome unknown"):
        prepare(publisher, draft)
    unknown = application.store.attempt(draft.id)
    assert unknown.state == "creating" and unknown.error.startswith("Create outcome unknown")
    provider.empty_lookup = True
    restarted = Publisher(Application(application.settings))
    with pytest.raises(ValueError):
        restarted.reconcile(draft.id, draft.revision)
    provider.empty_lookup = False
    assert restarted.reconcile(draft.id, draft.revision).state == "review_ready"
    assert provider.creates == Staging.stages == 1 and provider.updates == 0


def test_readiness_gates_both_new_write_stages_but_not_status(publication, application, draft):
    publisher, provider = publication
    application.settings.reverb_processed_photo_review_confirmed = False
    with pytest.raises(ValueError):
        prepare(publisher, draft)
    assert provider.creates == Staging.stages == 0
    application.settings.reverb_processed_photo_review_confirmed = True
    prepare(publisher, draft)
    application.settings.reverb_processed_photo_review_confirmed = False
    with pytest.raises(ValueError):
        approve(publisher, draft)
    assert provider.updates == 0
    assert publisher.reconcile(draft.id, draft.revision).state == "review_ready"
    application.settings.reverb_processed_photo_review_confirmed = True
    approve(publisher, draft)
    application.settings.reverb_processed_photo_review_confirmed = False
    assert publisher.reconcile(draft.id, draft.revision).state == "published"
    assert provider.creates == provider.updates == 1


def test_disabled_readiness_offers_no_final_grant_and_keeps_its_reason(
    publication, application, draft
):
    publisher, provider = publication
    prepare(publisher, draft)
    stale = publisher.processed_review(draft.id, draft.revision)["token"]
    application.settings.reverb_processed_photo_review_confirmed = False
    review = publisher.processed_review(draft.id, draft.revision)
    assert review["token"] is None and "disabled" in review["blocked"]
    client, _csrf = browser(application)
    page = client.get(f"/drafts/{draft.id}/processed")
    assert "disabled" in page.text and 'action="/drafts/' + draft.id + '/publish"' not in page.text
    with pytest.raises(ValueError, match="disabled"):
        publisher.publish(draft.id, draft.revision, stale)
    assert application.store.attempt(draft.id).state == "remote"
    application.settings.reverb_processed_photo_review_confirmed = True
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, stale)
    assert provider.updates == 0


@pytest.mark.parametrize(
    ("change", "message"),
    [("missing", "Configure REVERB_API_TOKEN"), ("rotated", "Restore the original binding")],
)
def test_local_binding_refusal_never_downgrades_verified_publication(
    publication, application, draft, change, message
):
    publisher, provider = publication
    prepare(publisher, draft)
    published = approve(publisher, draft)
    application.settings.reverb_api_token = None if change == "missing" else "rotated"
    with pytest.raises(ValueError, match=message):
        publisher.reconcile(draft.id, draft.revision)
    saved = application.store.attempt(draft.id)
    assert saved.state == "published" and saved.url == published.url
    assert provider.updates == 1


def test_reference_drift_is_explained_before_intent_and_never_unverifies_publication(
    publication, application, draft
):
    publisher, provider = publication
    prepare(publisher, draft)
    final = publisher.processed_review(draft.id, draft.revision)
    category = provider.reference_data["categories"][0]
    category["listable"] = False
    with pytest.raises(ValueError, match="Select an allowed Reverb condition and category"):
        publisher.publish(draft.id, draft.revision, final["token"])
    saved = application.store.attempt(draft.id)
    assert saved.state == "remote" and "Select an allowed Reverb" in saved.error
    category["listable"] = True
    assert publisher.reconcile(draft.id, draft.revision).state == "review_ready"
    published = approve(publisher, draft)
    category["listable"] = False
    result = publisher.reconcile(draft.id, draft.revision)
    assert result.state == "published" and result.url == published.url
    assert provider.updates == 1


def test_reference_drift_never_blocks_historical_read_only_status(
    publication, application, draft
):
    publisher, provider = publication
    prepare(publisher, draft)
    seed_old_attempt(application, draft, publisher, "published")
    restarted = Publisher(Application(application.settings))
    provider.remote["state"]["slug"] = "live"
    provider.reference_data["categories"][0]["listable"] = False
    with pytest.raises(ValueError, match="Historical write remains unverified"):
        restarted.reconcile(draft.id, draft.revision)
    historical = restarted.app.store.attempt(draft.id)
    assert historical.state == "historical_unverified" and historical.observed_state == "live"
    assert "Select an allowed" not in historical.error
    assert provider.updates == 0


def test_reference_drift_still_allows_read_only_sku_discovery(publication, application, draft):
    publisher, provider = publication
    provider.timeout_create = True
    with pytest.raises(ValueError, match="Create outcome unknown"):
        prepare(publisher, draft)
    category = provider.reference_data["categories"][0]
    category["listable"] = False
    with pytest.raises(ValueError, match="Select an allowed Reverb condition and category"):
        publisher.reconcile(draft.id, draft.revision)
    discovered = application.store.attempt(draft.id)
    assert discovered.state == "remote" and discovered.remote_id == "42"
    assert discovered.snapshot is None
    category["listable"] = True
    assert publisher.reconcile(draft.id, draft.revision).state == "review_ready"
    assert provider.creates == 1 and provider.updates == 0


@pytest.mark.parametrize(
    "evidence", ["id_only", "no_cover", "ambiguous_url", "duplicate", "invalid_id"]
)
def test_unreviewable_metadata_blocks_capture(publication, application, draft, evidence):
    publisher, provider = publication
    draft = application.upload([synthetic_image("blue")], draft.id, draft.revision)
    prepare(publisher, draft)
    if evidence == "id_only":
        del provider.remote["photos"][0]["url"]
    elif evidence == "no_cover":
        del provider.remote["_links"]["photo"]
    elif evidence == "ambiguous_url":
        provider.remote["photos"][0]["_links"] = {
            "full": {"href": provider.remote["photos"][1]["url"]}
        }
    elif evidence == "duplicate":
        provider.remote["photos"][1] = provider.remote["photos"][0]
    else:
        provider.remote["photos"][0]["id"] = True
    with pytest.raises(ValueError):
        publisher.reconcile(draft.id, draft.revision)
    assert application.store.attempt(draft.id).state == "remote"
    assert provider.updates == 0


def test_url_identities_are_hashed_and_duplicate_content_is_supported(
    publication, application, draft, image_bytes
):
    publisher, provider = publication
    draft = application.upload([image_bytes], draft.id, draft.revision)
    prepare(publisher, draft)
    for photo in provider.remote["photos"]:
        del photo["id"]
    snapshot = publisher.reconcile(draft.id, draft.revision).snapshot
    assert snapshot.gallery[0].digest == snapshot.gallery[1].digest
    assert snapshot.gallery[0].identity != snapshot.gallery[1].identity
    assert "secret-fixture" not in snapshot.model_dump_json()
    assert approve(publisher, draft).state == "published"


def test_mid_capture_metadata_change_cannot_issue_review(publication, application, draft):
    publisher, provider = publication
    prepare(publisher, draft)
    fetch = provider.photo_evidence

    def mutate(remote):
        entities = fetch(remote)
        provider.remote["title"] = "Changed while downloading"
        return entities

    with patch.object(provider, "photo_evidence", side_effect=mutate), pytest.raises(ValueError):
        publisher.reconcile(draft.id, draft.revision)
    assert application.store.attempt(draft.id).state == "remote"
    assert publisher.processed_review(draft.id, draft.revision)["token"] is None
    assert provider.updates == 0


def test_browser_serves_manifest_bytes_only_with_session_and_membership(
    publication, application, draft
):
    publisher, provider = publication
    draft = application.upload([synthetic_image("blue")], draft.id, draft.revision)
    provider.initial_images = [synthetic_image("orange"), synthetic_image("blue")]
    provider.cover_bytes = synthetic_image("orange", (30, 30))
    snapshot = prepare(publisher, draft).snapshot
    client, csrf = browser(application)
    page = client.get(f"/drafts/{draft.id}/processed")
    assert page.status_code == 200
    assert "secret-fixture" not in page.text and "secret-cover" not in page.text
    assert "checked" not in re.search(r'<input[^>]+name="approval"[^>]*>', page.text).group(0)
    assert page.text.index(snapshot.gallery[0].blob_id) < page.text.index(
        snapshot.gallery[1].blob_id
    )
    assert "Reverb cover" in page.text and snapshot.cover.blob_id in page.text
    assert all(photo.id in page.text for photo in draft.photos)
    assert "script-src 'none'" in page.headers["content-security-policy"]
    for entry in [*snapshot.gallery, snapshot.cover]:
        path = f"/snapshots/{draft.id}/{snapshot.id}/{entry.blob_id}"
        response = client.get(path)
        assert hashlib.sha256(response.content).hexdigest() == entry.digest
        assert response.headers["content-type"] == entry.media_type
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert TestClient(client.app, base_url=LOCAL).get(path).status_code == 403
    assert (
        client.get(f"/snapshots/{draft.id}/{'0' * 32}/{snapshot.cover.blob_id}").status_code == 404
    )
    assert client.get(f"/snapshots/{draft.id}/{snapshot.id}/{'0' * 32}").status_code == 404
    for action, approval in [
        ("prepare", "publish-reviewed-processed"),
        ("publish", "prepare-unpublished"),
    ]:
        assert (
            post(client, csrf, draft, action, token=token(page), approval=approval).status_code
            == 400
        )
    application.snapshots.path(snapshot.cover.blob_id).write_bytes(synthetic_image("red"))
    assert (
        client.get(f"/snapshots/{draft.id}/{snapshot.id}/{snapshot.cover.blob_id}").status_code
        == 400
    )
    assert (
        post(
            client, csrf, draft, "publish", token=token(page), approval="publish-reviewed-processed"
        ).status_code
        == 400
    )
    assert provider.updates == 0


def test_browser_two_unchecked_grants_and_status_is_read_only(publication, application, draft):
    _publisher, provider = publication
    client, csrf = browser(application)
    review = post(client, csrf, draft, "review")
    assert review.status_code == 200 and provider.creates == 0
    assert post(client, csrf, draft, "prepare", token=token(review)).status_code == 400
    processed = post(
        client, csrf, draft, "prepare", token=token(review), approval="prepare-unpublished"
    )
    assert processed.status_code == 200
    assert provider.creates == 1 and provider.updates == 0
    assert post(client, csrf, draft, "publish", token=token(processed)).status_code == 400
    assert (
        post(
            client,
            csrf,
            draft,
            "publish",
            token=token(processed),
            approval="publish-reviewed-processed",
        ).status_code
        == 200
    )
    assert post(client, csrf, draft, "status").status_code == 200
    assert provider.updates == 1


@pytest.mark.parametrize("status", [401, 403, 422, 400, 409, 429, 500, 503])
def test_definite_rejection_only_can_reopen_create(publication, application, draft, status):
    publisher, provider = publication
    provider.reject_create = status
    definite = status in (401, 403, 422)
    outcome = "No listing was created" if definite else "Create outcome unknown"
    with pytest.raises(ValueError, match=outcome + ".*Reverb POST failed"):
        prepare(publisher, draft)
    provider.reject_create = None
    if definite:
        assert application.store.attempt(draft.id) is None
        assert prepare(publisher, draft).state == "review_ready"
        assert provider.creates == 2
    else:
        assert application.store.attempt(draft.id).state == "creating"
        with pytest.raises(ValueError, match="Create outcome unknown.*Exact-SKU lookup"):
            publisher.reconcile(draft.id, draft.revision)
        assert provider.creates == 1
    assert provider.updates == 0


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (
            ClientError({"Error": {"Code": "AccessDenied"}}, "GetBucketLifecycle"),
            "No listing was created; correct configuration",
        ),
        (
            ValueError("Image staging needs an existing enabled one-day expiry."),
            "No listing was created.*one-day expiry",
        ),
    ],
)
def test_precreate_failure_and_abandoned_prepared_slot_allow_fresh_review(
    publication, application, draft, failure, message
):
    publisher, provider = publication
    Staging.failure = failure
    with pytest.raises(ValueError, match=message):
        prepare(publisher, draft)
    assert application.store.attempt(draft.id) is None
    Staging.failure = None
    application.store.save_attempt(
        Attempt(draft_id=draft.id, revision=draft.revision, correlation="old", fingerprint="old")
    )
    assert prepare(publisher, draft).state == "review_ready"
    assert provider.creates == 1


@pytest.mark.parametrize("change", ["revision", "photo_order", "credentials", "shop"])
def test_stale_preparation_never_uploads(publication, application, draft, change):
    publisher, provider = publication
    draft = application.upload([synthetic_image("blue")], draft.id, draft.revision)
    review = publisher.review(draft.id, draft.revision)
    if change == "revision":
        draft = application.edit(draft.id, draft.revision, {"title": "Owner edit", "price": "190"})
    elif change == "photo_order":
        draft = application.reorder(draft.id, draft.revision, [p.id for p in draft.photos][::-1])
    elif change == "credentials":
        application.settings.reverb_api_token = "rotated"
    else:
        provider.wrong_shop = True
    with pytest.raises(ValueError):
        publisher.prepare(draft.id, draft.revision, review["token"])
    assert provider.creates == Staging.stages == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("condition", ""),
        ("category_id", "wrong"),
        ("price", "0"),
        ("price_reason", ""),
        ("shipping", []),
        ("legacy_remote_id", "123"),
    ],
)
def test_owner_fields_and_legacy_duplicate_protection_still_block(
    publication, application, draft, field, value
):
    publisher, provider = publication
    setattr(draft, field, value)
    draft = application.store.save(draft, draft.revision)
    with pytest.raises(ValueError):
        publisher.review(draft.id, draft.revision)
    assert provider.creates == Staging.stages == 0


def test_unknown_shipping_region_is_not_silently_allowed(publication, application, draft):
    publisher, _provider = publication
    draft.shipping.append(ShippingRate(region_code="NOT_A_REGION", amount="50"))
    draft = application.store.save(draft, draft.revision)
    with pytest.raises(ValueError):
        publisher.review(draft.id, draft.revision)


def test_cleanup_failure_reports_live_without_replaying_publish(publication, application, draft):
    publisher, provider = publication
    prepare(publisher, draft)
    with patch.object(
        Staging, "cleanup", side_effect=ClientError({"Error": {"Code": "Denied"}}, "DeleteObject")
    ):
        result = approve(publisher, draft)
    assert result.state == "published" and result.url and result.staged_keys
    assert "cleanup pending" in result.error
    assert publisher.reconcile(draft.id, draft.revision).staged_keys == []
    assert provider.updates == 1


def seed_old_attempt(application, draft, publisher, state, *, proven=True):
    """Persist the actual pre-cutover schema, not a fake new-contract approval."""
    payload = publisher.payload(
        draft,
        {
            "conditions": [{"display_name": "Poor", "uuid": "poor-uuid"}],
            "categories": [{"uuid": "utility-uuid", "listable": True}],
            "regions": [{"code": "US_CON", "name": "Continental U.S."}],
        },
    )
    fingerprint = publisher.fingerprint(draft, payload)
    body = {
        "draft_id": draft.id,
        "revision": draft.revision,
        "correlation": f"synthshop-{draft.id}",
        "fingerprint": fingerprint,
        "state": state,
        "remote_id": None if state in ("prepared", "creating") else "42",
        "url": None,
        "error": "historical evidence",
        "staged_keys": [],
        "image_ids": ["old-id"],
        "image_digests": ["old-hash"],
    }
    with application.store.connect() as db:
        db.execute("DROP TABLE reviews")
        db.execute(
            "CREATE TABLE reviews (id TEXT PRIMARY KEY, token TEXT, revision INTEGER, fingerprint TEXT, payload TEXT)"
        )
        db.execute(
            "INSERT INTO reviews VALUES (?, ?, ?, ?, ?)",
            (
                draft.id,
                "old-token",
                draft.revision,
                fingerprint if proven else "unproven",
                json.dumps(payload),
            ),
        )
        db.execute("INSERT OR REPLACE INTO attempts VALUES (?, ?)", (draft.id, json.dumps(body)))
        db.execute("PRAGMA user_version=0")
    return body


@pytest.mark.parametrize(
    "state", ["prepared", "creating", "remote", "publishing", "published", "published_unverified"]
)
def test_old_state_migration_retains_history_and_never_manufactures_approval(
    publication, application, draft, state
):
    publisher, provider = publication
    prepare(publisher, draft)
    old = seed_old_attempt(application, draft, publisher, state)
    restarted = Publisher(Application(application.settings))
    with restarted.app.store.connect() as db:
        history = json.loads(
            db.execute("SELECT body FROM publication_history WHERE id=?", (draft.id,)).fetchone()[0]
        )
        assert history == old
        assert db.execute("SELECT 1 FROM reviews").fetchone() is None
    with pytest.raises(ValueError):
        restarted.publish(draft.id, draft.revision, "old-token")
    if state == "prepared":
        assert restarted.app.store.attempt(draft.id) is None
    elif state in ("creating", "remote"):
        assert restarted.reconcile(draft.id, draft.revision).state == "review_ready"
        saved = restarted.app.store.attempt(draft.id)
        assert saved.preparation.historical and saved.preparation.authorized_at is None
        assert saved.final_approval is None
        assert approve(restarted, draft).state == "published"
    else:
        provider.remote["state"]["slug"] = "live"
        with pytest.raises(ValueError):
            restarted.reconcile(draft.id, draft.revision)
        saved = restarted.app.store.attempt(draft.id)
        assert saved.state == "historical_unverified"
        assert saved.final_approval is None and saved.url is None
    assert provider.creates == 1
    assert provider.updates == (1 if state in ("creating", "remote") else 0)


def test_decline_cannot_reopen_historical_publish_for_a_second_put(
    publication, application, draft
):
    publisher, provider = publication
    prepare(publisher, draft)
    seed_old_attempt(application, draft, publisher, "publishing")
    restarted = Publisher(Application(application.settings))
    assert restarted.decline(draft.id, draft.revision).state == "historical_unverified"
    with pytest.raises(ValueError):
        restarted.reconcile(draft.id, draft.revision)
    saved = restarted.app.store.attempt(draft.id)
    assert saved.state == "historical_unverified" and saved.snapshot is None
    saved.state = "remote"
    with pytest.raises(ValueError):
        restarted.app.store.save_attempt(saved)
    assert provider.creates == 1 and provider.updates == 0


def test_decline_leaves_an_uncertain_create_unchanged(publication, application, draft):
    publisher, provider = publication
    provider.timeout_create = True
    with pytest.raises(ValueError):
        prepare(publisher, draft)
    assert publisher.decline(draft.id, draft.revision).state == "creating"
    assert application.store.attempt(draft.id).state == "creating"
    assert provider.creates == 1 and provider.updates == 0


def test_unproven_historical_binding_cannot_be_adopted(publication, application, draft):
    publisher, provider = publication
    prepare(publisher, draft)
    seed_old_attempt(application, draft, publisher, "remote", proven=False)
    restarted = Publisher(Application(application.settings))
    with pytest.raises(ValueError):
        restarted.reconcile(draft.id, draft.revision)
    saved = restarted.app.store.attempt(draft.id)
    assert saved.approved_payload is None and saved.snapshot is None
    assert provider.creates == 1 and provider.updates == 0


@respx.mock
def test_real_client_reads_documented_hal_web_links_and_does_not_retry_writes():
    settings = Settings(_env_file=None, reverb_api_token="test-only")
    respx.get("https://api.reverb.com/api/my/account").respond(200, json={})
    respx.get("https://api.reverb.com/api/shop").respond(
        200,
        json={
            "id": 1333667,
            "_links": {"web": {"href": "https://reverb.com/shop/bounceconnection"}},
        },
    )
    create = respx.post("https://api.reverb.com/api/listings").respond(
        202, json={"listing": {"id": 42}}
    )
    update = respx.put("https://api.reverb.com/api/listings/42").mock(
        side_effect=httpx.ReadTimeout("lost")
    )
    with ReverbClient(settings, authenticated=True) as client:
        assert client.verify_shop()["slug"] == "bounceconnection"
        assert client.create_draft({"sku": "one"})["id"] == 42
        assert json.loads(create.calls[0].request.content)["publish"] is False
        with pytest.raises(httpx.ReadTimeout):
            client.update("42", {"publish": True})
        assert update.call_count == 1
        with pytest.raises(ReverbAPIError):
            client.public_url({"_links": {"web": {"href": "https://evil.example/item/42"}}})


@pytest.mark.parametrize(
    "url",
    [
        "http://images.reverb.com/a.jpg",
        "https://images.reverb.com.evil.invalid/a.jpg",
        "https://127.0.0.1/a.jpg",
        "https://secret@images.reverb.com/a.jpg",
        "https://images.reverb.com:8443/a.jpg",
        "https://images.reverb.com/a.jpg#fragment",
    ],
)
@respx.mock
def test_photo_fetch_rejects_unsupported_destinations(url):
    with pytest.raises(ValueError):
        ReverbClient.fetch_photo("id", "1", "url", url)
    assert not respx.calls


@pytest.mark.parametrize(
    "kind",
    [
        "redirect",
        "missing",
        "empty",
        "oversized",
        "timeout",
        "mime",
        "encoding",
        "animation",
        "pixels",
        "corrupt",
    ],
)
@respx.mock
def test_unavailable_or_undecodable_entities_are_not_reviewable(kind):
    url = "https://images.reverb.com/a.jpg"
    route = respx.get(url)
    headers = {"Content-Type": "image/png"}
    content = synthetic_image("orange")
    if kind == "redirect":
        route.respond(302, headers={"Location": "https://127.0.0.1/private"})
    elif kind == "missing":
        route.respond(404)
    elif kind == "timeout":
        route.mock(side_effect=httpx.ReadTimeout("unavailable"))
    else:
        if kind == "empty":
            content = b""
        elif kind == "oversized":
            content = b"x" * (20 * 1024 * 1024 + 1)
        elif kind == "mime":
            headers["Content-Type"] = "image/jpeg"
        elif kind == "encoding":
            headers["Content-Encoding"] = "br"
        elif kind == "animation":
            stream = io.BytesIO()
            Image.new("RGB", (2, 2), "red").save(
                stream,
                "PNG",
                save_all=True,
                append_images=[Image.new("RGB", (2, 2), "blue")],
                duration=20,
            )
            content = stream.getvalue()
        elif kind == "corrupt":
            content = b"not an image"
        route.respond(200, content=content, headers=headers)
    if kind == "pixels":
        with patch("synthshop.core.snapshots.MAX_PIXELS", 1), pytest.raises(ValueError):
            ReverbClient.fetch_photo("id", "1", "url", url)
    else:
        with pytest.raises((ValueError, httpx.DecodingError)):
            ReverbClient.fetch_photo("id", "1", "url", url)
    assert len(respx.calls) == 1


def test_malformed_final_preflight_revokes_approval_even_after_recovery(
    publication, application, draft
):
    publisher, provider = publication
    prepare(publisher, draft)
    final = publisher.processed_review(draft.id, draft.revision)
    provider.remote["state"] = None
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, final["token"])
    provider.remote["state"] = {"slug": "draft"}
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, final["token"])
    assert application.store.attempt(draft.id).state == "remote"
    assert provider.updates == 0


def test_partial_snapshot_write_cannot_leave_usable_capture(publication, application, draft):
    publisher, provider = publication
    put = application.snapshots.put
    count = 0

    def fail_second(entry, content):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("full disk")
        put(entry, content)

    with (
        patch.object(application.snapshots, "put", side_effect=fail_second),
        pytest.raises(ValueError),
    ):
        prepare(publisher, draft)
    attempt = application.store.attempt(draft.id)
    assert attempt.remote_id == "42" and attempt.snapshot is None
    assert list(application.snapshots.root.iterdir()) == []
    with pytest.raises(ValueError):
        publisher.processed_review(draft.id, draft.revision)
    assert provider.creates == 1 and provider.updates == 0


@pytest.mark.parametrize("change", ["envelope", "profile", "symlink"])
def test_snapshot_integrity_and_profile_are_required_for_final_approval(
    publication, application, draft, change, tmp_path
):
    publisher, provider = publication
    snapshot = prepare(publisher, draft).snapshot
    final = publisher.processed_review(draft.id, draft.revision)
    attempt = application.store.attempt(draft.id)
    if change == "symlink":
        blob = application.snapshots.path(snapshot.cover.blob_id)
        external = tmp_path / "outside.png"
        external.write_bytes(blob.read_bytes())
        blob.unlink()
        blob.symlink_to(external)
    else:
        attempt.snapshot.profile = "different-negotiation-profile"
        if change == "profile":
            attempt.snapshot.seal()
        application.store.save_attempt(attempt)
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, final["token"])
    assert provider.updates == 0


def test_consumed_manifest_and_intent_cannot_be_replaced(publication, application, draft):
    publisher, _provider = publication
    prepare(publisher, draft)
    approved = approve(publisher, draft)
    altered = approved.model_copy(deep=True)
    altered.snapshot.cover.digest = "0" * 64
    altered.snapshot.seal()
    with pytest.raises(ValueError):
        application.store.save_attempt(altered)
    altered = approved.model_copy(deep=True)
    altered.publish_intent_at = None
    with pytest.raises(ValueError):
        application.store.save_attempt(altered)
    assert application.store.attempt(draft.id).snapshot == approved.snapshot


def test_wrong_production_destination_blocks_before_staging(publication, application, draft):
    publisher, provider = publication
    review = publisher.review(draft.id, draft.revision)
    application.settings.expected_shop_id = "other-shop"
    with pytest.raises(ValueError):
        publisher.prepare(draft.id, draft.revision, review["token"])
    assert provider.creates == Staging.stages == 0
