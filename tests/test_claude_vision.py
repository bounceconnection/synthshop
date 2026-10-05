"""Photo content decoding and owner fact precedence through regeneration."""

import io
from unittest.mock import patch

import pytest
from PIL import Image

from synthshop.core.models import Candidate


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


def test_generated_copy_does_not_import_model_unit_claims(application, draft):
    draft.description = ""
    draft = application.store.save(draft, draft.revision)
    candidate = Candidate(make="Example", model="Meter", description="Mint fully tested with USB")
    with patch("synthshop.core.application.identify_from_photos", return_value=candidate):
        result = application.analyze(draft.id, draft.revision)
    assert "fully tested" not in result.description
    assert "USB" not in result.description
    assert "Mint" not in result.description
    assert result.condition == "Poor"


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
