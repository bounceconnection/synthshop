"""Durability, stale tabs, tampering, and legacy duplicate prevention."""

import json

import pytest

from synthshop.core.application import Application
from synthshop.core.models import Attempt
from synthshop.core.product_store import DraftConflictError, DraftStore


def test_restart_preserves_photos_facts_and_revision(application, draft):
    reopened = Application(application.settings)
    restored = reopened.store.load(draft.id)
    assert restored.model_dump() == draft.model_dump()
    reopened.photos.verify(restored.photos)
    changed = application.edit(
        draft.id, draft.revision, {"faults": "Dead left input", "price": "180"}
    )
    with pytest.raises(DraftConflictError):
        reopened.edit(draft.id, draft.revision, {"faults": "No faults"})
    assert reopened.store.load(draft.id).faults == "Dead left input"
    assert changed.revision == draft.revision + 1


def test_attempt_freezes_edits(application, draft):
    application.store.save_attempt(
        Attempt(draft_id=draft.id, revision=draft.revision, correlation="one", fingerprint="hash")
    )
    with pytest.raises(DraftConflictError):
        application.edit(draft.id, draft.revision, {"title": "Changed"})
    assert application.store.load(draft.id).title == draft.title


def test_import_linked_legacy_records_once_without_mutating_original(application, tmp_path):
    directory = tmp_path / "old"
    directory.mkdir()
    old = {
        "make": "Old",
        "model": "Synth",
        "price": 120.0,
        "status": "listed",
        "reverb": {"listing_id": 12345},
    }
    path = directory / "old.json"
    path.write_text(json.dumps(old))
    assert application.store.import_legacy(directory) == []
    assert DraftStore(application.store.root).import_legacy(directory) == []
    records = application.store.list_all()
    assert len(records) == 1
    assert records[0].legacy_remote_id == "12345"
    assert records[0].legacy_status == "listed"
    assert json.loads(path.read_text()) == old


def test_malformed_legacy_is_reported_not_skipped(application, tmp_path):
    (tmp_path / "bad.json").write_text("not json")
    assert application.store.import_legacy(tmp_path)


def test_photo_tampering_breaks_review(application, draft):
    application.photos.path(draft.photos[0].id).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        application.photos.verify(draft.photos)


def test_order_is_exact_permutation(application, image_bytes):
    item = application.upload([image_bytes, image_bytes])
    ids = [photo.id for photo in item.photos]
    with pytest.raises(ValueError):
        application.reorder(item.id, item.revision, [ids[0], ids[0]])
    updated = application.reorder(item.id, item.revision, ids[::-1])
    assert [photo.id for photo in updated.photos] == ids[::-1]
