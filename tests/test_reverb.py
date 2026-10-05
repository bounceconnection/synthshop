"""Behavioral approval/recovery tests. Fakes are not provider compatibility evidence."""

from copy import deepcopy
from unittest.mock import patch

import httpx
import pytest

from synthshop.core.models import ShippingRate
from synthshop.core.product_store import DraftConflictError
from synthshop.core.publishing import Publisher


class Provider:
    """Stateful remote double for ambiguous outcomes and ownership failures."""

    def __init__(self, references):
        self.reference_data = references
        self.remote = None
        self.creates = 0
        self.updates = 0
        self.timeout_create = False
        self.timeout_publish = False
        self.empty_lookup = False
        self.wrong_shop = False
        self.drop_photos = False

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
        self.remote = deepcopy(payload)
        self.remote.update(
            id=42,
            shop_id=1333667,
            state={"slug": "draft"},
            photos=[] if self.drop_photos else [{"id": 101}],
            _links={"self": {"web": {"href": "https://reverb.com/item/42"}}},
        )
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

    def public_url(self, _listing):
        return "https://reverb.com/item/42"


class Staging:
    """No external storage side effects in tests."""

    def __init__(self, _settings):
        pass

    def stage(self, *_args):
        return ["https://example.invalid/approved-photo.jpg"]

    def cleanup(self, *_args):
        pass


@pytest.fixture
def publication(application, references):
    application.settings.reverb_api_token = "test-only"
    application.settings.r2_account_id = "example"
    application.settings.r2_access_key_id = "test-only"
    application.settings.r2_secret_access_key = "test-only"
    provider = Provider(references)
    with (
        patch("synthshop.core.publishing.ReverbClient", return_value=provider),
        patch("synthshop.core.publishing.PhotoStaging", Staging),
    ):
        yield Publisher(application), provider


def test_explicit_review_then_one_publish_across_repeated_clicks(publication, draft):
    publisher, provider = publication
    review = publisher.review(draft.id, draft.revision)
    assert provider.creates == 0
    first = publisher.publish(draft.id, draft.revision, review["token"])
    second = publisher.publish(draft.id, draft.revision, review["token"])
    assert first.state == second.state == "published"
    assert first.remote_id == second.remote_id == "42"
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


def test_photo_order_and_environment_invalidate_review(
    publication, application, draft, image_bytes
):
    publisher, provider = publication
    two = application.upload([image_bytes], draft.id, draft.revision)
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
