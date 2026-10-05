"""SQLite drafts, optimistic revisions, durable attempts, and local process locks."""

import fcntl
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from synthshop.core.models import Attempt, Draft


class DraftConflictError(ValueError):
    """A stale tab or active remote attempt must not overwrite reviewed state."""


class DraftStore:
    """All mutations are atomic. Original JSON remains untouched on migration."""

    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.database = self.root / "drafts.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, revision INTEGER,
                    body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, token TEXT,
                    revision INTEGER, fingerprint TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS imports (path TEXT PRIMARY KEY, draft_id TEXT);
            """)
        self.database.chmod(0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Commit/rollback and close each short-lived database connection."""
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def load(self, draft_id: str) -> Draft:
        """Load latest revision, without resolving user-controlled paths."""
        with self.connect() as db:
            row = db.execute("SELECT body FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if row is None:
            raise KeyError("Draft not found")
        return Draft.model_validate_json(row["body"])

    def list_all(self) -> list[Draft]:
        """Newest first; invalid records are errors, never silently discarded."""
        with self.connect() as db:
            return [
                Draft.model_validate_json(row[0])
                for row in db.execute("SELECT body FROM drafts ORDER BY rowid DESC")
            ]

    def save(self, draft: Draft, expected: int | None = None) -> Draft:
        """CAS update invalidates any previous approval, including photo order edits."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM drafts WHERE id=?", (draft.id,)).fetchone()
            if db.execute("SELECT 1 FROM attempts WHERE id=?", (draft.id,)).fetchone():
                raise DraftConflictError(
                    "Publication attempt exists. Reconcile it; this draft is locked."
                )
            if row is not None and row[0] != expected:
                raise DraftConflictError("This tab is stale. Reload before saving or approving.")
            if row is None and expected is not None:
                raise DraftConflictError("Draft no longer exists")
            draft.revision = 1 if row is None else expected + 1
            db.execute(
                "INSERT OR REPLACE INTO drafts VALUES (?, ?, ?)",
                (draft.id, draft.revision, draft.model_dump_json()),
            )
            db.execute("DELETE FROM reviews WHERE id=?", (draft.id,))
        return draft

    def attempt(self, draft_id: str) -> Attempt | None:
        """Recover the single opportunity, including ambiguous create outcomes."""
        with self.connect() as db:
            row = db.execute("SELECT body FROM attempts WHERE id=?", (draft_id,)).fetchone()
        return Attempt.model_validate_json(row[0]) if row else None

    def save_attempt(self, attempt: Attempt) -> None:
        """Persist remote IDs before any further network operation."""
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO attempts VALUES (?, ?)",
                (attempt.draft_id, attempt.model_dump_json()),
            )

    def release(self, attempt: Attempt) -> None:
        """Drop an attempt that never sent a create, so the owner can correct and re-approve."""
        with self.connect() as db:
            db.execute("DELETE FROM attempts WHERE id=?", (attempt.draft_id,))

    def review(self, draft: Draft, token: str, fingerprint: str, payload: dict) -> None:
        """Snapshot a review, not permission to write remotely."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT revision FROM drafts WHERE id=?", (draft.id,)).fetchone()
            if not current or current[0] != draft.revision:
                raise DraftConflictError("Draft changed during review. Reload.")
            db.execute(
                "INSERT OR REPLACE INTO reviews VALUES (?, ?, ?, ?, ?)",
                (draft.id, token, draft.revision, fingerprint, json.dumps(payload)),
            )

    def claim(self, draft: Draft, token: str, fingerprint: str) -> Attempt:
        """Approval and attempt reservation are one transaction; no duplicate POST slot."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            review = db.execute("SELECT * FROM reviews WHERE id=?", (draft.id,)).fetchone()
            current = db.execute("SELECT revision FROM drafts WHERE id=?", (draft.id,)).fetchone()
            if (
                not review
                or review["token"] != token
                or review["fingerprint"] != fingerprint
                or review["revision"] != draft.revision
                or current[0] != draft.revision
            ):
                raise DraftConflictError(
                    "Approval is missing or stale. Review the exact current revision."
                )
            row = db.execute("SELECT body FROM attempts WHERE id=?", (draft.id,)).fetchone()
            if row:
                attempt = Attempt.model_validate_json(row[0])
                if attempt.state != "prepared":
                    if attempt.fingerprint != fingerprint:
                        raise DraftConflictError(
                            "Environment changed after attempt. Restore it to reconcile."
                        )
                    return attempt
            attempt = Attempt(
                draft_id=draft.id,
                revision=draft.revision,
                correlation=f"synthshop-{draft.id}",
                fingerprint=fingerprint,
            )
            db.execute(
                "INSERT OR REPLACE INTO attempts VALUES (?, ?)",
                (draft.id, attempt.model_dump_json()),
            )
            return attempt

    @contextmanager
    def publish_lock(self) -> Iterator[None]:
        """Serialize remote workflows across threads and restarted/duplicate servers."""
        with (self.root / "publish.lock").open("a", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DraftConflictError(
                    "A publish/reconciliation is already in progress."
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def import_legacy(self, directory: Path) -> list[str]:
        """Import every record once. Linked/non-draft products are never fresh creates."""
        errors = []
        for path in sorted(directory.glob("*.json")):
            key = str(path.resolve())
            with self.connect() as db:
                if db.execute("SELECT 1 FROM imports WHERE path=?", (key,)).fetchone():
                    continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                remote = data.get("reverb") or {}
                draft = Draft(
                    make=data["make"],
                    model=data["model"],
                    variant=data.get("variant") or "",
                    title=" ".join(
                        filter(None, [data["make"], data["model"], data.get("variant")])
                    ),
                    description=data.get("description", ""),
                    condition=data.get("condition", ""),
                    price=str(data["price"]),
                    offers_enabled=data.get("offers_enabled", True),
                    legacy_remote_id=str(remote["listing_id"])
                    if remote.get("listing_id")
                    else None,
                    legacy_status=data.get("status", "draft"),
                    migration_note="Imported legacy JSON. Re-upload current photos; recheck facts, "
                    "condition, category, shipping and price. Original record retained.",
                    owner_fields=["make", "model", "variant", "description", "title", "condition"],
                )
                # Atomic import marker plus initial draft, so a restart cannot duplicate imports.
                with self.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    if db.execute("SELECT 1 FROM imports WHERE path=?", (key,)).fetchone():
                        continue
                    db.execute(
                        "INSERT INTO drafts VALUES (?, 1, ?)", (draft.id, draft.model_dump_json())
                    )
                    db.execute("INSERT INTO imports VALUES (?, ?)", (key, draft.id))
            except (ValueError, KeyError, TypeError, OSError):
                errors.append(
                    f"Could not import {path.name}; original retained. Fix it before publishing."
                )
        return errors
