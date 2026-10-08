"""Draft HTTP submissions retain edits and cannot bypass review/revision safety."""

import json
from decimal import Decimal
from unittest.mock import Mock

import httpx
import pytest

from fastapi.testclient import TestClient

from synthshop.web.app import create_app
from tests.test_reverb import rendered_form

LOCAL = "http://127.0.0.1:8765"


def test_invalid_review_keeps_all_edits_and_reports_all_fields(
    application, image_bytes, references, monkeypatch
):
    item = application.upload([image_bytes])
    provider = Mock()
    provider.__enter__ = Mock(return_value=provider)
    provider.__exit__ = Mock(return_value=False)
    provider.references.return_value = references
    monkeypatch.setattr("synthshop.core.publishing.ReverbClient", Mock(return_value=provider))
    web = create_app(application.settings)
    client = TestClient(web, base_url=LOCAL)
    client.get(f"/unlock?key={web.state.unlock}")
    fields = {
        "csrf": web.state.csrf, "revision": item.revision,
        "make": "  Newly typed maker  ", "model": "", "description": "",
        "title": "My title", "condition": "Not a condition", "category_id": "unknown",
        "price": "not money", "price_reason": "", "international_rates": "CA=fifty",
        "variant": "<custom>", "offers_enabled": "on",
    }
    response = client.post(
        f"/drafts/{item.id}/review", data=fields, headers={"Origin": LOCAL}
    )
    assert response.status_code == 400
    assert response.template.name == "draft.html"
    assert response.context["values"]["make"] == fields["make"]
    assert response.context["values"]["price"] == "not money"
    assert response.context["values"]["international_rates"] == "CA=fifty"
    assert set(response.context["errors"]) == {
        "model", "description", "condition", "category_id", "price", "price_reason",
        "international_rates",
    }
    assert "make" not in response.context["errors"]
    assert application.store.load(item.id) == item
    assert application.store.attempt(item.id) is None
    provider.verify_shop.assert_not_called()
    provider.create.assert_not_called()


@pytest.fixture
def editor(application, references, monkeypatch):
    provider = Mock()
    provider.__enter__ = Mock(return_value=provider)
    provider.__exit__ = Mock(return_value=False)
    provider.references.return_value = references
    monkeypatch.setattr("synthshop.core.publishing.ReverbClient", Mock(return_value=provider))
    staging = Mock(side_effect=AssertionError("Review must never stage photos"))
    monkeypatch.setattr("synthshop.core.publishing.PhotoStaging", staging)
    web = create_app(application.settings)
    client = TestClient(web, base_url=LOCAL)
    client.get(f"/unlock?key={web.state.unlock}")
    return client, web.state.csrf, provider


def submit(editor, draft, fields, action="review"):
    client, csrf, _provider = editor
    return client.post(
        f"/drafts/{draft.id}/{action}",
        data={"csrf": csrf, "revision": draft.revision, **fields},
        headers={"Origin": LOCAL},
    )


def test_correcting_invalid_review_saves_visible_maker_before_review(
    application, image_bytes, editor
):
    item = application.upload([image_bytes])
    fields = {
        "make": "", "model": "", "title": "", "description": "",
        "condition": "", "category_id": "", "price": "", "price_reason": "",
    }
    invalid = submit(editor, item, fields)
    assert set(invalid.context["errors"]) == set(fields)
    assert application.store.load(item.id) == item
    fields.update(
        make="Visible maker", model="Visible model", title="Owner title",
        description="Owner description", condition="Poor", category_id="utility-uuid",
        price="100.00", price_reason="Owner override", international_rates="CA=20.00",
    )
    reviewed = submit(editor, item, fields)
    assert reviewed.status_code == 200
    assert reviewed.template.name == "review.html"
    saved = application.store.load(item.id)
    assert saved.make == "Visible maker"
    assert saved.model == "Visible model"
    assert saved.revision == item.revision + 1
    assert reviewed.context["draft"] == saved
    assert reviewed.context["payload"]["make"] == saved.make
    assert reviewed.context["payload"]["shipping"]["rates"][1]["rate"]["amount"] == "20.00"
    assert application.store.attempt(item.id) is None
    client, csrf, provider = editor
    rejected = client.post(
        f"/drafts/{item.id}/prepare",
        data={"csrf": csrf, "revision": item.revision, "token": reviewed.context["token"],
              "approval": "prepare-unpublished"},
        headers={"Origin": LOCAL},
    )
    assert rejected.status_code == 409
    provider.create.assert_not_called()


@pytest.mark.parametrize("action", ["save", "review"])
@pytest.mark.parametrize("price", ["NaN", "Infinity", "-1", "1.001", "1000000000000"])
def test_tampered_money_retained_without_saving(application, draft, editor, action, price):
    response = submit(editor, draft, {
        "make": "Unsaved correction", "price": price, "international_rates": "CA=not-money",
    }, action)
    assert response.status_code == 400
    assert response.template.name == "draft.html"
    assert set(response.context["errors"]) == {"price", "international_rates"}
    assert response.context["values"]["price"] == price
    assert response.context["values"]["make"] == "Unsaved correction"
    assert application.store.load(draft.id) == draft
    assert application.store.attempt(draft.id) is None


def test_partial_save_allowed_but_invalid_text_preserved(application, image_bytes, editor):
    item = application.upload([image_bytes])
    too_long = "x" * 12001
    invalid = submit(editor, item, {
        "make": "Visible maker", "model": too_long, "title": "x" * 256, "price_reason": too_long,
    }, "save")
    assert set(invalid.context["errors"]) == {"model", "title", "price_reason"}
    assert invalid.context["values"]["model"] == too_long
    assert application.store.load(item.id) == item
    saved = submit(editor, item, {"make": "Visible maker"}, "save")
    assert saved.status_code == 200
    assert application.store.load(item.id).make == "Visible maker"
    assert application.store.load(item.id).description == ""


def test_stale_review_is_conflict_not_field_error(application, draft, editor):
    changed = application.edit(draft.id, draft.revision, {"make": "Newer maker"})
    response = submit(editor, draft, {"make": "", "price": "invalid"})
    assert response.status_code == 409
    assert response.template.name == "error.html"
    assert application.store.load(draft.id) == changed
    editor[2].references.assert_not_called()


def test_race_during_review_does_not_overwrite_newer_revision(application, draft, editor):
    provider = editor[2]
    references = provider.references.return_value

    def concurrent_edit():
        application.edit(draft.id, draft.revision, {"make": "Concurrent maker"})
        return references

    provider.references.side_effect = concurrent_edit
    response = submit(editor, draft, {"make": "Stale maker"})
    assert response.status_code == 409
    assert response.template.name == "error.html"
    assert application.store.load(draft.id).make == "Concurrent maker"
    assert application.store.attempt(draft.id) is None


def test_provider_failure_does_not_save_or_become_field_error(application, draft, editor):
    editor[2].references.side_effect = httpx.ConnectError("offline")
    response = submit(editor, draft, {"make": "Unsaved maker"})
    assert response.status_code == 502
    assert response.template.name == "error.html"
    assert application.store.load(draft.id) == draft


def test_csrf_and_bad_revision_remain_distinct(application, draft, editor):
    for fields, status in (({"csrf": "wrong"}, 403), ({"revision": "tampered"}, 400)):
        response = submit(editor, draft, fields)
        assert response.status_code == status
        assert response.template.name == "error.html"
    assert application.store.load(draft.id) == draft
    editor[2].references.assert_not_called()


def test_unknown_destination_is_field_error(application, draft, editor):
    response = submit(editor, draft, {"international_rates": "MARS=20.00"})
    assert response.status_code == 400
    assert set(response.context["errors"]) == {"international_rates"}
    assert response.context["values"]["international_rates"] == "MARS=20.00"
    assert application.store.load(draft.id) == draft


def recommended(application, draft):
    """Two reviewed sold observations; price and reasoning follow their recommendation."""
    rows = [
        {
            "provider": "eBay", "url": f"https://www.ebay.com/itm/{number}",
            "source_id": str(number), "source_class": "sold_display",
            "provenance": "Owner observed sold page", "title": "Example Instruments Meter Stereo",
            "amount": amount, "shipping": "0", "shipping_region": "US_CON",
        }
        for number, amount in ((1, "200.00"), (2, "220.00"))
    ]
    item = application.import_evidence(draft.id, draft.revision, json.dumps(rows))
    for comp_id in [comp.id for comp in item.evidence]:
        item = application.review_evidence(item.id, item.revision, comp_id, True, "Exact")
    return application.edit(item.id, item.revision, {"price": "", "price_reason": ""})


def test_identity_change_explains_stale_recommendation_then_save_permits_same_amount(
    application, draft, editor
):
    client = editor[0]
    item = recommended(application, draft)
    assert item.price == Decimal("210.00")
    assert "price" not in item.owner_fields
    form = rendered_form(client.get(f"/drafts/{item.id}").text, "draft-fields")
    assert form["price"] == "210.00"
    form["make"] = "Corrected Instruments"
    for _resubmission in range(2):
        stale = client.post(f"/drafts/{item.id}/review", data=form, headers={"Origin": LOCAL})
        assert stale.status_code == 400
        assert stale.template.name == "draft.html"
        assert set(stale.context["errors"]) == {"price", "price_reason"}
        assert "Save owner corrections & copy first" in stale.context["errors"]["price"]
        assert "follow the supported recommendation" not in stale.text
        form = rendered_form(stale.text, "draft-fields")
        assert form["price"] == "210.00"
        assert form["make"] == "Corrected Instruments"
    assert application.store.load(item.id) == item
    saved = client.post(f"/drafts/{item.id}/save", data=form, headers={"Origin": LOCAL})
    assert saved.status_code == 200
    form = rendered_form(saved.text, "draft-fields")
    assert form["make"] == "Corrected Instruments"
    assert form["price"] == form["price_reason"] == ""
    form.update(price="210.00", price_reason="Owner reviewed after identity correction")
    reviewed = client.post(f"/drafts/{item.id}/review", data=form, headers={"Origin": LOCAL})
    assert reviewed.status_code == 200
    assert reviewed.template.name == "review.html"
    assert reviewed.context["payload"]["make"] == "Corrected Instruments"
    assert reviewed.context["payload"]["price"]["amount"] == "210.00"
    assert {"price", "price_reason"} <= set(application.store.load(item.id).owner_fields)
    assert application.store.attempt(item.id) is None


def test_local_review_blocker_keeps_entries_as_form_error(application, draft, editor):
    draft.legacy_remote_id = "existing-123"
    draft = application.store.save(draft, draft.revision)
    response = submit(editor, draft, {"make": "Unsaved maker", "price": "200.00"})
    assert response.status_code == 400
    assert response.template.name == "draft.html"
    assert not response.context["errors"]
    assert "cannot become a fresh create" in response.context["blocker"]
    assert response.context["values"]["make"] == "Unsaved maker"
    assert response.context["values"]["price"] == "200.00"
    assert application.store.load(draft.id) == draft
    assert application.store.attempt(draft.id) is None
    editor[2].verify_shop.assert_not_called()


def test_prepare_reports_specific_field_errors(application, draft, editor, references):
    for name, value in {
        "reverb_api_token": "test-only", "r2_account_id": "example",
        "r2_access_key_id": "test-only", "r2_secret_access_key": "test-only",
        "reverb_processed_photo_review_confirmed": True,
    }.items():
        setattr(application.settings, name, value)
    editor[2].references.return_value = {**references, "conditions": []}
    response = submit(
        editor, draft, {"token": "unused", "approval": "prepare-unpublished"}, "prepare"
    )
    assert response.status_code == 400
    assert response.template.name == "error.html"
    assert response.context["message"] == "Select an allowed Reverb condition."
    assert application.store.load(draft.id) == draft
    assert application.store.attempt(draft.id) is None
