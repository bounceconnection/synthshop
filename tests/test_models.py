"""Consumer-visible money and evidence boundaries."""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from synthshop.core.models import Comparable, Draft
from synthshop.core.pricing import from_reverb, recommendation


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0.001"])
def test_invalid_money_rejected(value):
    with pytest.raises(ValidationError):
        Draft(price=value)


def comp(amount="190.00", **changes):
    return Comparable(
        provider="Reverb",
        url="https://reverb.com/item/123",
        source_id="123",
        source_class="sold_display",
        provenance="Owner observed sold page",
        title="Example Meter",
        amount=amount,
        shipping="0",
        shipping_region="US_CON",
        approved_match=True,
        **changes,
    )


def test_no_mixing_unknown_shipping_currency_asks_and_duplicates():
    item = Draft(make="Example", model="Meter")
    item.evidence_identity = item.identity()
    first = comp()
    duplicate = comp()
    unknown = comp()
    unknown.source_id = "124"
    unknown.shipping = None
    foreign = comp()
    foreign.source_id = "125"
    foreign.listing_currency = "EUR"
    active = comp()
    active.source_id = "126"
    active.source_class = "active_ask"
    item.evidence = [first, duplicate, unknown, foreign, active]
    result = recommendation(item)
    assert result["ask"] is None
    assert "Duplicate" in result["excluded"][duplicate.id]
    assert "shipping" in result["excluded"][unknown.id]
    assert "currency" in result["excluded"][foreign.id]


def test_traceable_decimal_recommendation_and_stale_identity():
    item = Draft(make="Example", model="Meter")
    first = comp("190.00")
    second = comp("200.01")
    second.source_id = "124"
    second.shipping = Decimal("10.00")
    item.evidence = [first, second]
    item.evidence_identity = item.identity()
    result = recommendation(item)
    assert result["ask"] == "200.00"
    assert result["sources"] == [first.id, second.id]
    assert result["source_class"] == "sold_display"
    item.model = "Other"
    assert recommendation(item)["ask"] is None


def test_automatic_wrong_variant_and_date_semantics():
    item = Draft(make="Original", model="Cloud", condition="Good")
    row = {
        "id": 1,
        "make": "Clone",
        "model": "Cloud",
        "title": "Cloud clone bundle",
        "state": {"slug": "live"},
        "price": {"amount": "99", "currency": "USD"},
        "listing_currency": "EUR",
        "published_at": "2026-01-01",
        "condition": {"display_name": "Mint"},
        "_links": {
            "self": {"href": "https://api.reverb.com/api/listings/1"},
            "web": {"href": "https://reverb.com/item/1-cloud-clone-bundle"},
        },
    }
    result = from_reverb(row, item, sold=True)
    assert str(result.url) == "https://reverb.com/item/1-cloud-clone-bundle"
    assert result.approved_match is False
    assert "maker" in result.excluded_reason
    assert "state" in result.excluded_reason
    assert "Condition" in result.excluded_reason
    assert result.sale_date is None
    assert result.shipping is None
    assert result.listing_currency == "EUR"


def test_unowned_price_follows_evidence_and_owner_price_wins(application, draft):
    rows = [
        {
            "provider": "eBay",
            "url": f"https://www.ebay.com/itm/{number}",
            "source_id": str(number),
            "source_class": "sold_display",
            "provenance": "Owner observed sold page",
            "title": "Example Instruments Meter Stereo",
            "amount": amount,
            "shipping": "0",
            "shipping_region": "US_CON",
        }
        for number, amount in ((1, "200.00"), (2, "220.00"))
    ]
    item = application.import_evidence(draft.id, draft.revision, json.dumps(rows))
    first, second = (observation.id for observation in item.evidence)

    def include(current, comp_id, keep=True):
        return application.review_evidence(current.id, current.revision, comp_id, keep, "Exact")

    item = include(include(item, first), second)
    assert item.price == Decimal("190.00")
    assert item.price_reason == "Owner price, insufficient market evidence"
    item = application.edit(item.id, item.revision, {"price": "", "price_reason": ""})
    assert item.price == Decimal("210.00")
    assert "median of 2 reviewed sold_display" in item.price_reason
    form = {"price": str(item.price), "price_reason": item.price_reason}
    item = application.edit(item.id, item.revision, form)
    item = include(item, second, keep=False)
    assert item.price is None
    assert item.price_reason == ""
    item = include(item, second)
    assert item.price == Decimal("210.00")
    item = application.edit(item.id, item.revision, {"price": "230", "price_reason": ""})
    item = include(item, second, keep=False)
    assert item.price == Decimal("230.00")
    assert item.price_reason == ""


def test_malformed_money_input_names_the_field(application, draft):
    with pytest.raises(ValueError, match="Asking price"):
        application.edit(draft.id, draft.revision, {"price": "one ninety"})
    row = {
        "provider": "eBay",
        "url": "https://www.ebay.com/itm/1",
        "source_class": "sold_display",
        "provenance": "Owner observed sold page",
        "title": "Example",
        "amount": "one ninety",
    }
    with pytest.raises(ValueError, match="Observation 1: check amount"):
        application.import_evidence(draft.id, draft.revision, json.dumps([row]))
    assert application.store.load(draft.id).revision == draft.revision
