"""Behavioral approval/recovery tests. Fakes are not provider compatibility evidence."""

import io
from copy import deepcopy
from unittest.mock import patch

import httpx
import pytest
import respx
from botocore.exceptions import ClientError
from PIL import Image

from synthshop.core.application import Application
from synthshop.core.config import Settings
from synthshop.core.models import Attempt, ShippingRate
from synthshop.core.product_store import DraftConflictError
from synthshop.core.publishing import Publisher
from synthshop.integrations.reverb import ReverbAPIError, ReverbClient


class Provider(ReverbClient):
    """Stateful remote double; inherited response parsing (public_url) is the real client's."""

    def __init__(self, references, settings):
        self.settings = settings
        self.reference_data = references
        self.remote = None
        self.creates = 0
        self.updates = 0
        self.reject_create = None
        self.malformed_price = False
        self.timeout_create = False
        self.timeout_publish = False
        self.empty_lookup = False
        self.wrong_shop = False
        self.drop_photos = False
        self.initial_images = None
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
        return {"id": "1333667", "slug": "bounceconnection", "name": "bounce connection"}

    def create_draft(self, payload):
        self.creates += 1
        if self.reject_create:
            raise ReverbAPIError("Reverb POST failed", self.reject_create)
        self.remote = deepcopy(payload)
        images = self.initial_images
        if images is None:
            images = [Staging.images[url] for url in payload["photos"]]
        self.image_bytes = {
            f"https://images.reverb.com/image/upload/photo-{index}.jpg": content
            for index, content in enumerate(images)
        }
        self.remote.update(
            id=42,
            shop_id=1333667,
            state={"slug": "draft"},
            photos=[]
            if self.drop_photos
            else [{"id": 101 + index, "url": url} for index, url in enumerate(self.image_bytes)],
            _links={
                "self": {"href": "https://api.reverb.com/api/listings/42"},
                "web": {"href": "https://reverb.com/item/42-example-meter"},
                "photo": {"href": next(iter(self.image_bytes))},
            },
        )
        if self.malformed_price:
            self.remote["price"]["amount"] = "n/a"
        if self.timeout_create:
            raise httpx.ReadTimeout("response lost")
        return deepcopy(self.remote)

    def own_listings(self, _sku):
        return [] if self.empty_lookup or self.remote is None else [deepcopy(self.remote)]

    def get_listing(self, _listing_id):
        return deepcopy(self.remote)

    def verify_ownership(self, listing, _sku):
        if listing["shop_id"] != 1333667:
            raise ValueError("Ownership mismatch")

    def update(self, _listing_id, payload):
        self.updates += 1
        self.remote.update(payload)
        self.remote["state"] = {"slug": "live"}
        if self.timeout_publish:
            raise httpx.ReadTimeout("publish response lost")


class Staging:
    """No external storage side effects in tests."""

    failure = None
    images = {}

    def __init__(self, _settings):
        pass

    def stage(self, draft, _attempt, _store, photos):
        if Staging.failure:
            raise Staging.failure
        Staging.images = {
            f"https://example.invalid/{photo.id}.jpg": photos.path(photo.id).read_bytes()
            for photo in draft.photos
        }
        return list(Staging.images)

    def cleanup(self, *_args):
        pass


@pytest.fixture
def publication(application, references):
    application.settings.reverb_api_token = "test-only"
    application.settings.r2_account_id = "example"
    application.settings.r2_access_key_id = "test-only"
    application.settings.r2_secret_access_key = "test-only"
    provider = Provider(references, application.settings)
    Staging.failure = None
    with (
        patch("synthshop.core.publishing.ReverbClient", return_value=provider),
        patch("synthshop.core.publishing.PhotoStaging", Staging),
        respx.mock(assert_all_called=False) as network,
    ):

        def serve_image(request):
            assert "authorization" not in request.headers
            assert "cookie" not in request.headers
            return httpx.Response(200, content=provider.image_bytes[str(request.url)])

        network.get(host="images.reverb.com").mock(side_effect=serve_image)
        yield Publisher(application), provider


def synthetic_image(color):
    """Distinct generated inputs, never inventory or provider image downloads."""
    stream = io.BytesIO()
    Image.new("RGB", (96, 64), color).save(stream, "PNG")
    return stream.getvalue()


@pytest.mark.parametrize("case", ["reversed", "substituted"])
def test_first_read_must_match_approved_derivatives(publication, application, draft, case):
    publisher, provider = publication
    draft = application.upload([synthetic_image("blue")], draft.id, draft.revision)
    approved = [application.photos.path(photo.id).read_bytes() for photo in draft.photos]
    provider.initial_images = (
        approved[::-1]
        if case == "reversed"
        else [synthetic_image("green"), synthetic_image("purple")]
    )
    review = publisher.review(draft.id, draft.revision)
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, review["token"])
    attempt = application.store.attempt(draft.id)
    assert provider.updates == 0
    assert provider.creates == 1
    assert attempt.state == "remote"
    assert not attempt.image_ids
    assert not attempt.image_digests


def test_binding_is_durable_before_publish_and_survives_restart(publication, application, draft):
    publisher, provider = publication
    draft = application.upload([synthetic_image("blue")], draft.id, draft.revision)
    review = publisher.review(draft.id, draft.revision)
    with (
        patch.object(provider, "update", side_effect=SystemExit("process stopped")),
        pytest.raises(SystemExit),
    ):
        publisher.publish(draft.id, draft.revision, review["token"])
    restarted = Application(application.settings)
    attempt = restarted.store.attempt(draft.id)
    assert attempt.state == "publishing"
    assert attempt.image_ids == ["101", "102"]
    assert attempt.image_digests == [photo.digest for photo in draft.photos]
    assert provider.updates == 0
    result = Publisher(restarted).publish(draft.id, draft.revision, review["token"])
    assert result.state == "published"
    assert result.image_ids == attempt.image_ids
    assert result.image_digests == attempt.image_digests
    assert provider.creates == provider.updates == 1


@pytest.mark.parametrize("change", ["order", "identity", "bytes", "cover", "missing"])
def test_restart_rejects_changed_binding_then_reconciles_without_write(
    publication, application, draft, change
):
    publisher, provider = publication
    draft = application.upload([synthetic_image("blue")], draft.id, draft.revision)
    review = publisher.review(draft.id, draft.revision)
    provider.timeout_publish = True
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, review["token"])
    before = application.store.attempt(draft.id)
    remote, images = deepcopy(provider.remote), dict(provider.image_bytes)
    if change == "order":
        provider.remote["photos"].reverse()
    elif change == "identity":
        provider.remote["photos"][0]["id"] = 999
    elif change == "bytes":
        provider.image_bytes[provider.remote["photos"][0]["url"]] = synthetic_image("green")
    elif change == "cover":
        provider.remote["_links"]["photo"]["href"] = provider.remote["photos"][1]["url"]
    else:
        provider.remote["photos"].pop()
    restarted = Application(application.settings)
    with pytest.raises(ValueError):
        Publisher(restarted).publish(draft.id, draft.revision, review["token"])
    after = restarted.store.attempt(draft.id)
    assert after.state == "publishing"
    assert after.url is None
    assert after.image_ids == before.image_ids
    assert after.image_digests == before.image_digests
    assert provider.creates == provider.updates == 1
    provider.remote, provider.image_bytes = remote, images
    result = Publisher(restarted).publish(draft.id, draft.revision, review["token"])
    assert result.state == "published"
    assert provider.creates == provider.updates == 1


def test_legacy_ids_cannot_bypass_correspondence(publication, application, draft):
    publisher, provider = publication
    review = publisher.review(draft.id, draft.revision)
    provider.timeout_create = True
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, review["token"])
    legacy = application.store.attempt(draft.id)
    legacy.image_ids = ["101"]
    application.store.save_attempt(legacy)
    url = provider.remote["photos"][0]["url"]
    approved = provider.image_bytes[url]
    provider.image_bytes[url] = synthetic_image("green")
    with pytest.raises(ValueError):
        Publisher(Application(application.settings)).publish(
            draft.id, draft.revision, review["token"]
        )
    assert provider.updates == 0
    assert not application.store.attempt(draft.id).image_digests
    provider.image_bytes[url] = approved
    result = publisher.publish(draft.id, draft.revision, review["token"])
    assert result.image_digests == [draft.photos[0].digest]
    assert result.state == "published"
    assert provider.creates == provider.updates == 1


def test_published_drift_revokes_success_without_enabling_relist(publication, application, draft):
    publisher, provider = publication
    review = publisher.review(draft.id, draft.revision)
    publisher.publish(draft.id, draft.revision, review["token"])
    url = provider.remote["photos"][0]["url"]
    approved = provider.image_bytes[url]
    provider.image_bytes[url] = synthetic_image("green")
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, review["token"])
    saved = application.store.attempt(draft.id)
    assert saved.state == "published_unverified"
    assert saved.url is None
    provider.image_bytes[url] = approved
    provider.remote["state"]["slug"] = "draft"
    with pytest.raises(ValueError):
        Publisher(Application(application.settings)).publish(
            draft.id, draft.revision, review["token"]
        )
    assert provider.creates == provider.updates == 1


def test_post_publish_photo_substitution_is_not_success(publication, application, draft):
    publisher, provider = publication
    review = publisher.review(draft.id, draft.revision)
    update = provider.update

    def change_after_publish(listing_id, payload):
        update(listing_id, payload)
        provider.image_bytes[provider.remote["photos"][0]["url"]] = synthetic_image("green")

    with (
        patch.object(provider, "update", side_effect=change_after_publish),
        pytest.raises(ValueError),
    ):
        publisher.publish(draft.id, draft.revision, review["token"])
    saved = application.store.attempt(draft.id)
    assert saved.state == "publishing"
    assert saved.url is None
    assert saved.image_digests == [draft.photos[0].digest]
    assert provider.creates == provider.updates == 1


@pytest.mark.parametrize("evidence", ["id_only", "no_cover", "ambiguous_url", "duplicate"])
def test_unverifiable_photo_metadata_never_publishes(publication, application, draft, evidence):
    publisher, provider = publication
    draft = application.upload([synthetic_image("blue")], draft.id, draft.revision)
    review = publisher.review(draft.id, draft.revision)
    provider.timeout_create = True
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, review["token"])
    if evidence == "id_only":
        del provider.remote["photos"][0]["url"]
    elif evidence == "no_cover":
        del provider.remote["_links"]["photo"]
    elif evidence == "ambiguous_url":
        provider.remote["photos"][0]["_links"] = {
            "full": {"href": provider.remote["photos"][1]["url"]}
        }
    else:
        provider.remote["photos"][1] = provider.remote["photos"][0]
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, review["token"])
    saved = application.store.attempt(draft.id)
    assert saved.state == "remote"
    assert not saved.image_ids
    assert not saved.image_digests
    assert provider.creates == 1
    assert provider.updates == 0


def test_duplicate_approved_derivatives_refused_before_remote_create(
    publication, application, draft, image_bytes
):
    publisher, provider = publication
    draft = application.upload([image_bytes], draft.id, draft.revision)
    with pytest.raises(ValueError):
        publisher.review(draft.id, draft.revision)
    assert application.store.attempt(draft.id) is None
    assert provider.creates == provider.updates == 0


def test_explicit_review_then_one_publish_across_repeated_clicks(publication, draft):
    publisher, provider = publication
    review = publisher.review(draft.id, draft.revision)
    assert provider.creates == 0
    first = publisher.publish(draft.id, draft.revision, review["token"])
    second = publisher.publish(draft.id, draft.revision, review["token"])
    assert first.state == second.state == "published"
    assert first.remote_id == second.remote_id == "42"
    assert first.url == "https://reverb.com/item/42-example-meter"
    assert provider.creates == 1
    assert provider.updates == 1
    assert provider.remote["condition"] == {"uuid": "poor-uuid"}
    assert provider.remote["shipping"]["rates"] == [
        {"region_code": "US_CON", "rate": {"amount": "0.00", "currency": "USD"}}
    ]


def test_missing_or_stale_approval_never_creates(publication, application, draft):
    publisher, provider = publication
    with pytest.raises(DraftConflictError):
        publisher.publish(draft.id, draft.revision, "guessed-token")
    review = publisher.review(draft.id, draft.revision)
    changed = application.edit(draft.id, draft.revision, {"title": "Owner edit", "price": "190"})
    with pytest.raises(DraftConflictError):
        publisher.publish(changed.id, changed.revision, review["token"])
    assert provider.creates == 0


def test_photo_order_and_environment_invalidate_review(publication, application, draft):
    publisher, provider = publication
    two = application.upload([synthetic_image("blue")], draft.id, draft.revision)
    review = publisher.review(two.id, two.revision)
    changed = application.reorder(two.id, two.revision, [p.id for p in two.photos][::-1])
    with pytest.raises(DraftConflictError):
        publisher.publish(changed.id, changed.revision, review["token"])
    review = publisher.review(changed.id, changed.revision)
    application.settings.reverb_api_token = "rotated-test-token"
    with pytest.raises(DraftConflictError):
        publisher.publish(changed.id, changed.revision, review["token"])
    assert provider.creates == 0


def test_ambiguous_create_empty_lookup_never_reposts(publication, application, draft):
    publisher, provider = publication
    review = publisher.review(draft.id, draft.revision)
    provider.timeout_create = True
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, review["token"])
    assert application.store.attempt(draft.id).state == "creating"
    provider.empty_lookup = True
    with pytest.raises(ValueError):
        Publisher(application).publish(draft.id, draft.revision, review["token"])
    assert provider.creates == 1
    provider.empty_lookup = False
    result = Publisher(application).publish(draft.id, draft.revision, review["token"])
    assert result.remote_id == "42"
    assert result.state == "published"
    assert provider.creates == 1


def test_publish_timeout_reconciles_without_second_write(publication, application, draft):
    publisher, provider = publication
    review = publisher.review(draft.id, draft.revision)
    provider.timeout_publish = True
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, review["token"])
    assert application.store.attempt(draft.id).remote_id == "42"
    result = publisher.publish(draft.id, draft.revision, review["token"])
    assert result.state == "published"
    assert provider.creates == provider.updates == 1


def test_missing_photos_or_wrong_ownership_never_false_success(publication, application, draft):
    publisher, provider = publication
    review = publisher.review(draft.id, draft.revision)
    provider.drop_photos = True
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, review["token"])
    assert application.store.attempt(draft.id).remote_id == "42"
    assert provider.updates == 0
    provider.remote["photos"] = [{"id": 101}]
    provider.remote["shop_id"] = 999
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, review["token"])
    assert provider.updates == 0
    assert application.store.attempt(draft.id).state != "published"


def test_wrong_shop_before_attempt_does_not_create(publication, application, draft):
    publisher, provider = publication
    review = publisher.review(draft.id, draft.revision)
    provider.wrong_shop = True
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, review["token"])
    assert provider.creates == 0
    assert application.store.attempt(draft.id) is None


def test_reference_ids_and_shipping_must_be_authoritative(publication, application, draft):
    publisher, provider = publication
    draft.category_id = "studio-gear"
    draft = application.store.save(draft, draft.revision)
    with pytest.raises(ValueError, match="category"):
        publisher.review(draft.id, draft.revision)
    draft.category_id = "utility-uuid"
    draft.shipping.append(ShippingRate(region_code="NOT_A_REGION", amount="50"))
    draft = application.store.save(draft, draft.revision)
    with pytest.raises(ValueError, match="destination"):
        publisher.review(draft.id, draft.revision)
    assert provider.creates == 0


def test_linked_legacy_record_is_never_fresh_create(publication, application, draft):
    publisher, provider = publication
    draft.legacy_remote_id = "existing-123"
    draft = application.store.save(draft, draft.revision)
    with pytest.raises(ValueError, match="fresh create"):
        publisher.review(draft.id, draft.revision)
    assert provider.creates == 0


def test_failure_before_create_releases_draft_for_correction(publication, application, draft):
    publisher, provider = publication
    review = publisher.review(draft.id, draft.revision)
    Staging.failure = ClientError({"Error": {"Code": "AccessDenied"}}, "GetBucketLifecycle")
    with pytest.raises(ValueError, match="No listing was created"):
        publisher.publish(draft.id, draft.revision, review["token"])
    assert application.store.attempt(draft.id) is None
    assert provider.creates == 0
    Staging.failure = None
    application.settings.r2_secret_access_key = "corrected-test-only"
    corrected = application.edit(
        draft.id, draft.revision, {"title": "Corrected title", "price": "190"}
    )
    review = publisher.review(corrected.id, corrected.revision)
    assert publisher.publish(corrected.id, corrected.revision, review["token"]).state == "published"
    assert provider.creates == 1


def test_prepared_leftover_never_blocks_fresh_approval(publication, application, draft):
    publisher, provider = publication
    application.store.save_attempt(
        Attempt(
            draft_id=draft.id,
            revision=draft.revision,
            correlation=f"synthshop-{draft.id}",
            fingerprint="previous-environment",
        )
    )
    review = publisher.review(draft.id, draft.revision)
    assert publisher.publish(draft.id, draft.revision, review["token"]).state == "published"
    assert provider.creates == 1


@pytest.mark.parametrize("status", [401, 403, 422])
def test_definite_create_rejection_permits_fresh_approval(publication, application, draft, status):
    publisher, provider = publication
    review = publisher.review(draft.id, draft.revision)
    provider.reject_create = status
    with pytest.raises(ValueError, match="No listing was created. Reverb POST failed"):
        publisher.publish(draft.id, draft.revision, review["token"])
    assert application.store.attempt(draft.id) is None
    provider.reject_create = None
    review = publisher.review(draft.id, draft.revision)
    assert publisher.publish(draft.id, draft.revision, review["token"]).state == "published"
    assert provider.creates == 2


@pytest.mark.parametrize("status", [400, 409, 429, 500, 503])
def test_other_create_failures_stay_locked_without_repost(publication, application, draft, status):
    publisher, provider = publication
    review = publisher.review(draft.id, draft.revision)
    provider.reject_create = status
    with pytest.raises(ValueError, match="not verified"):
        publisher.publish(draft.id, draft.revision, review["token"])
    assert application.store.attempt(draft.id).state == "creating"
    provider.reject_create = None
    with pytest.raises(ValueError, match="not verified"):
        publisher.publish(draft.id, draft.revision, review["token"])
    assert provider.creates == 1
    with pytest.raises(DraftConflictError):
        application.edit(draft.id, draft.revision, {"title": "Changed"})


def test_sent_attempt_still_bound_to_publishing_environment(publication, application, draft):
    publisher, provider = publication
    review = publisher.review(draft.id, draft.revision)
    provider.timeout_create = True
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, review["token"])
    application.settings.r2_bucket_name = "other-bucket"
    review = publisher.review(draft.id, draft.revision)
    with pytest.raises(DraftConflictError, match="Environment changed"):
        publisher.publish(draft.id, draft.revision, review["token"])
    assert application.store.attempt(draft.id).state == "creating"
    assert provider.creates == 1


def test_malformed_remote_amount_is_recorded_on_attempt(publication, application, draft):
    publisher, provider = publication
    review = publisher.review(draft.id, draft.revision)
    provider.malformed_price = True
    with pytest.raises(ValueError):
        publisher.publish(draft.id, draft.revision, review["token"])
    attempt = application.store.attempt(draft.id)
    assert attempt.remote_id == "42"
    assert attempt.state == "remote"
    assert attempt.error.startswith("Provider operation not verified")
    assert provider.updates == 0


@respx.mock
def test_real_client_reads_documented_hal_web_links():
    settings = Settings(_env_file=None, reverb_api_token="test-only")
    respx.get("https://api.reverb.com/api/my/account").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.get("https://api.reverb.com/api/shop").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 1333667,
                "name": "bounce connection",
                "_links": {
                    "self": {"href": "https://api.reverb.com/api/shop"},
                    "web": {"href": "https://reverb.com/shop/bounceconnection"},
                },
            },
        )
    )
    listing = {
        "_links": {
            "self": {"href": "https://api.reverb.com/api/listings/42"},
            "web": {"href": "https://reverb.com/item/42-example-meter"},
        }
    }
    with ReverbClient(settings, authenticated=True) as client:
        assert client.verify_shop()["slug"] == "bounceconnection"
        assert client.public_url(listing) == "https://reverb.com/item/42-example-meter"
        with pytest.raises(ReverbAPIError):
            client.public_url({"_links": {"web": {"href": "https://evil.example/item/42"}}})


@pytest.mark.parametrize(
    "url",
    [
        "http://images.reverb.com/image/upload/a.jpg",
        "https://images.reverb.com.evil.invalid/a.jpg",
        "https://127.0.0.1/a.jpg",
        "https://secret@images.reverb.com/a.jpg",
        "https://images.reverb.com:8443/a.jpg",
    ],
)
@respx.mock
def test_photo_evidence_never_fetches_unsupported_destinations(url):
    with pytest.raises(ReverbAPIError):
        ReverbClient.photo_digest(url)
    assert not respx.calls


@pytest.mark.parametrize("response", ["redirect", "missing", "empty", "oversized", "timeout"])
@respx.mock
def test_unavailable_served_image_is_not_identity_evidence(response):
    url = "https://images.reverb.com/image/upload/a.jpg"
    route = respx.get(url)
    if response == "redirect":
        route.respond(302, headers={"Location": "http://127.0.0.1/private"})
    elif response == "missing":
        route.respond(404)
    elif response == "empty":
        route.respond(200)
    elif response == "oversized":
        route.respond(200, content=b"x" * (20 * 1024 * 1024 + 1))
    else:
        route.mock(side_effect=httpx.ReadTimeout("unavailable"))
    with pytest.raises(ReverbAPIError):
        ReverbClient.photo_digest(url)
    assert len(respx.calls) == 1
