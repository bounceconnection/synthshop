"""Consumer-visible money and evidence boundaries."""

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
    }
    result = from_reverb(row, item, sold=True)
    assert result.approved_match is False
    assert "maker" in result.excluded_reason
    assert "state" in result.excluded_reason
    assert "Condition" in result.excluded_reason
    assert result.sale_date is None
    assert result.shipping is None
    assert result.listing_currency == "EUR"
