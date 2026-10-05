"""Photo content decoding and owner fact precedence through regeneration."""

import base64
import io
import json
from decimal import Decimal
from functools import partial
from unittest.mock import patch

import httpx
import openai
import pytest
from PIL import Image

from synthshop.core.models import Candidate
from tests.test_cli import LOCAL, browser


def test_content_not_filename_and_stripped_orientation(application):
    image = Image.new("RGB", (100, 50), "blue")
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private original metadata"
    stream = io.BytesIO()
    image.save(stream, "JPEG", exif=exif)
    item = application.upload([stream.getvalue()])
    with Image.open(application.photos.path(item.photos[0].id)) as stored:
        assert stored.size == (50, 100)
        assert not stored.getexif()
    assert (
        application.photos.path(item.photos[0].id, original=True).read_bytes() == stream.getvalue()
    )


def test_webp_real_content_accepted(application, image_bytes):
    item = application.upload([image_bytes])
    assert item.photos[0].original_format == "WEBP"
    with Image.open(application.photos.path(item.photos[0].id)) as stored:
        assert stored.format == "JPEG"
        assert stored.size == (96, 64)


@pytest.mark.parametrize("content", [b"", b"not an image", b"x" * (20 * 1024 * 1024 + 1)])
def test_invalid_photo_rejected(application, content):
    with pytest.raises(ValueError):
        application.upload([content])
    assert application.store.list_all() == []


def test_owner_condition_and_copy_win_over_model(application, draft):
    owned = application.edit(
        draft.id,
        draft.revision,
        {
            "make": "Owner maker",
            "condition": "Non functioning",
            "faults": "Left input dead",
            "included": "Meter\nPower supply",
            "title": "Owner title",
            "description": "Owner wording",
            "price": "190",
        },
    )
    candidate = Candidate(
        make="Guessed maker",
        model="Other",
        title="Mint rare unit",
        description="Works perfectly with USB cable",
        confidence="low",
        questions=["Please confirm rear model label."],
    )
    with patch("synthshop.core.application.identify_from_photos", return_value=candidate):
        regenerated = application.analyze(owned.id, owned.revision)
    assert regenerated.make == "Owner maker"
    assert regenerated.condition == "Non functioning"
    assert regenerated.description == "Owner wording"
    assert regenerated.title == "Owner title"
    assert regenerated.faults == "Left input dead"
    assert regenerated.included == "Meter\nPower supply"
    assert regenerated.candidate.questions == ["Please confirm rear model label."]


def test_generated_copy_fills_unsaved_editable_copy(application, draft):
    candidate = Candidate(
        make="Example",
        model="Meter",
        title="Example Meter w/ Breakout Cable + Power Supply",
        description="Included:\n-Meter\n-Breakout cable\n-Power supply",
        questions=["How was the unit tested?"],
    )
    with patch("synthshop.core.application.identify_from_photos", return_value=candidate):
        result = application.analyze(draft.id, draft.revision)
    assert result.title == candidate.title
    assert result.description == candidate.description
    assert result.condition == "Poor"
    assert result.price == Decimal("190.00")
    edited = application.edit(result.id, result.revision, {"title": "", "price": "190"})
    assert edited.title == "Example Meter"


def test_manual_facts_fill_empty_copy_without_testing_claim(application, image_bytes):
    item = application.upload([image_bytes])
    saved = application.edit(
        item.id,
        item.revision,
        {"make": "TC Electronic", "model": "Clarity M", "included": "Clarity M\n12V supply"},
    )
    assert saved.title == "TC Electronic Clarity M"
    assert saved.description == "Included:\n-Clarity M\n-12V supply"
    assert saved.testing == ""
    assert "test" not in saved.description.casefold()


def test_saving_unchanged_candidate_accepts_it_as_owner_text(application, draft):
    owned = application.edit(
        draft.id,
        draft.revision,
        {
            "make": draft.make,
            "title": draft.title,
            "description": draft.description,
            "price": "190",
        },
    )
    candidate = Candidate(
        make="Different maker",
        model="Different model",
        title="Different title",
        description="Fully tested",
    )
    with patch("synthshop.core.application.identify_from_photos", return_value=candidate):
        regenerated = application.analyze(owned.id, owned.revision)
    assert regenerated.make == draft.make
    assert regenerated.title == draft.title
    assert regenerated.description == draft.description


def response_body(text, *, status="completed", refusal=False):
    """A Responses API wire result; doubles never exercise a real model."""
    block = (
        {"type": "refusal", "refusal": text}
        if refusal
        else {"type": "output_text", "text": text, "annotations": []}
    )
    return {
        "id": "resp_test",
        "object": "response",
        "status": status,
        "output": [
            {"id": "msg_test", "type": "message", "role": "assistant",
             "status": "completed", "content": [block]}
        ],
    }


@pytest.fixture
def openai_response(application, respx_mock, monkeypatch):
    application.settings.openai_api_key = "test-only"
    # Explicit transport: SDK versions may change their default HTTP implementation.
    monkeypatch.setattr(
        "synthshop.integrations.openai_vision.openai.OpenAI",
        partial(openai.OpenAI, http_client=httpx.Client(
            transport=httpx.MockTransport(respx_mock.handler),
        )),
    )
    return respx_mock.post("https://api.openai.com/v1/responses")


def test_openai_proposal_persists_without_publishing(application, draft, openai_response):
    proposal = Candidate(
        make="Example Instruments", model="Meter Stereo", confidence="low",
        observations="Rear label is not readable.", questions=["Please photograph the label."],
        title="Example Instruments Meter Stereo", description="Scratched panel.",
    )
    openai_response.respond(200, json=response_body(proposal.model_dump_json()))
    client, csrf = browser(application)
    response = client.post(
        f"/drafts/{draft.id}/analyze",
        data={"csrf": csrf, "revision": draft.revision}, headers={"Origin": LOCAL},
    )
    assert response.status_code == 200
    saved = application.store.load(draft.id)
    assert saved.revision == draft.revision + 1
    assert saved.candidate == proposal
    assert saved.title == proposal.title
    assert saved.description == proposal.description
    assert saved.condition == "Poor"
    assert saved.testing == ""
    assert saved.price == Decimal("190.00")
    assert application.store.attempt(draft.id) is None


@pytest.mark.parametrize(
    "body",
    [
        response_body("private refusal details", refusal=True),
        response_body(Candidate().model_dump_json(), status="incomplete"),
        response_body("not json: private response details"),
        response_body('{"make":"Partial"}'),
        response_body(Candidate().model_dump_json().replace('"low"', '"certain"')),
        response_body(json.dumps({**Candidate().model_dump(), "price": 195})),
        {"status": "completed", "output": []},
        {"status": "completed", "output": None},
        {},
    ],
    ids=["refusal", "incomplete", "invalid-json", "missing-fields", "invalid-confidence",
         "invented-price", "missing-output", "null-output", "empty-response"],
)
def test_bad_output_keeps_saved_draft(application, draft, openai_response, body):
    openai_response.respond(200, json=body)
    client, csrf = browser(application)
    response = client.post(
        f"/drafts/{draft.id}/analyze",
        data={"csrf": csrf, "revision": draft.revision}, headers={"Origin": LOCAL},
    )
    assert response.status_code == 400
    assert "private refusal details" not in response.text
    assert "private response details" not in response.text
    assert application.store.load(draft.id) == draft
    assert application.store.attempt(draft.id) is None
    assert openai_response.call_count == 1


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_provider_failure_keeps_saved_draft(application, draft, openai_response, status):
    openai_response.respond(status, json={"error": {"message": "private provider details"}})
    with pytest.raises(ValueError) as error:
        application.analyze(draft.id, draft.revision)
    assert "private provider details" not in str(error.value)
    assert application.store.load(draft.id) == draft
    assert openai_response.call_count == 1


def test_connection_failure_keeps_saved_draft(application, draft, openai_response):
    openai_response.mock(side_effect=httpx.ConnectError("private network details"))
    with pytest.raises(ValueError) as error:
        application.analyze(draft.id, draft.revision)
    assert "private network details" not in str(error.value)
    assert application.store.load(draft.id) == draft
    assert openai_response.call_count == 1


def test_missing_key_keeps_saved_draft(application, draft, respx_mock):
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        application.analyze(draft.id, draft.revision)
    assert application.store.load(draft.id) == draft
    assert not respx_mock.calls


def test_openai_regeneration_preserves_owner_overrides(application, draft, openai_response):
    owned = application.edit(
        draft.id, draft.revision,
        {"make": "Owner maker", "model": "Owner model", "variant": "Owner variant",
         "title": "My title", "description": "My description", "condition": "Non functioning",
         "testing": "Not tested", "included": "Unit\nAdapter", "faults": "Dead left input",
         "price": "210", "price_reason": "Private owner reasoning"},
    )
    openai_response.respond(200, json=response_body(Candidate(
        make="Wrong maker", model="Wrong model", variant="Wrong variant",
        title="Wrong title", description="Wrong description",
    ).model_dump_json()))
    result = application.analyze(owned.id, owned.revision)
    for field in owned.owner_fields:
        assert getattr(result, field) == getattr(owned, field)


def test_only_item_derivatives_and_relevant_facts_leave_backend(
    application, draft, image_bytes, openai_response,
):
    other = application.upload([image_bytes])
    draft.price_reason = "Private pricing rationale"
    draft.research_note = "Private market research"
    draft.migration_note = "Private legacy inventory"
    draft.condition = "Poor"
    draft.included = "Unit\nAdapter"
    draft = application.store.save(draft, draft.revision)
    openai_response.respond(200, json=response_body(Candidate().model_dump_json()))
    application.analyze(draft.id, draft.revision)
    request = openai_response.calls[0].request
    payload = json.loads(request.content)
    content = payload["input"][0]["content"]
    images = [part for part in content if part["type"] == "input_image"]
    assert len(images) == len(draft.photos)
    for part, photo in zip(images, draft.photos, strict=True):
        prefix, encoded = part["image_url"].split(",", 1)
        assert prefix == "data:image/jpeg;base64"
        assert base64.b64decode(encoded) == application.photos.path(photo.id).read_bytes()
    text = next(part["text"] for part in content if part["type"] == "input_text")
    facts = json.loads(text.removeprefix("Owner facts: "))
    assert facts == draft.owner_facts()
    assert "condition" not in facts
    for private in (
        draft.price_reason, draft.research_note, draft.migration_note, draft.id,
        other.id, other.photos[0].id, str(application.store.root), "test-only",
    ):
        assert private not in request.content.decode()
    assert payload["store"] is False


def strict_schema_violations(node, path="$"):
    """Places where a JSON Schema breaks OpenAI strict Structured Outputs rules."""
    if isinstance(node, list):
        return [
            found for index, child in enumerate(node)
            for found in strict_schema_violations(child, f"{path}[{index}]")
        ]
    if not isinstance(node, dict):
        return []
    found = [f"{path}.default"] if "default" in node else []
    if node.get("type") == "object":
        if node.get("additionalProperties") is not False:
            found.append(f"{path}.additionalProperties")
        if set(node.get("required", ())) != set(node.get("properties", {})):
            found.append(f"{path}.required")
    for key, child in node.items():
        found += strict_schema_violations(child, f"{path}.{key}")
    return found


def test_request_demands_strict_complete_candidate(application, draft, openai_response):
    openai_response.respond(200, json=response_body(Candidate().model_dump_json()))
    application.analyze(draft.id, draft.revision)
    output_format = json.loads(openai_response.calls[0].request.content)["text"]["format"]
    assert output_format["type"] == "json_schema"
    assert output_format["strict"] is True
    schema = output_format["schema"]
    assert set(schema["properties"]) == set(Candidate.model_fields)
    assert strict_schema_violations(schema) == []
