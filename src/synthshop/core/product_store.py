"""SQLite drafts, optimistic revisions, durable attempts, and local process locks."""

import fcntl
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from synthshop.core.models import (
    CONTRACT,
    Attempt,
    Draft,
    FinalApproval,
    PreparationGrant,
    ProcessedSnapshot,
    now,
)


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
        self._migrate()

    def _migrate(self) -> None:
        """One locked cutover: archive history, invalidate tokens, never reopen sent writes."""
        with self.connect() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] >= 1:
                return
        with self.publish_lock(), self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("PRAGMA user_version").fetchone()[0] >= 1:
                return
            db.execute(
                "CREATE TABLE IF NOT EXISTS publication_history "
                "(id TEXT PRIMARY KEY, body TEXT NOT NULL, review TEXT)"
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(reviews)")}
            for name in ("purpose", "snapshot_id", "manifest_digest", "contract"):
                if name not in columns:
                    db.execute(f"ALTER TABLE reviews ADD COLUMN {name} TEXT")
            for row in db.execute("SELECT id, body FROM attempts").fetchall():
                old = json.loads(row["body"])
                review = db.execute("SELECT * FROM reviews WHERE id=?", (row["id"],)).fetchone()
                retained = dict(review) if review else None
                if retained:
                    retained.pop("token", None)
                db.execute(
                    "INSERT OR IGNORE INTO publication_history VALUES (?, ?, ?)",
                    (row["id"], row["body"], json.dumps(retained)),
                )
                # Prepared is demonstrably unsent, and the cross-process lock is held.
                if old["state"] == "prepared" and not old.get("remote_id"):
                    db.execute("DELETE FROM attempts WHERE id=?", (row["id"],))
                    continue
                old.pop("image_ids", None)
                old.pop("image_digests", None)
                old["contract"] = "historical"
                old["url"] = None
                old["error"] = "Historical attempt: no processed-photo publication approval."
                if old["state"] not in ("creating", "remote"):
                    old["state"] = "historical_unverified"
                if (
                    review
                    and review["fingerprint"] == old["fingerprint"]
                    and review["revision"] == old["revision"]
                ):
                    old["approved_payload"] = json.loads(review["payload"])
                migrated = Attempt.model_validate(old)
                db.execute(
                    "UPDATE attempts SET body=? WHERE id=?", (migrated.model_dump_json(), row["id"])
                )
            db.execute("DELETE FROM reviews")
            db.execute("PRAGMA user_version=1")

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
        self.attempt(draft.id)
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
        """Recover the single opportunity. Prepared sent nothing; it locks only mid-publish."""
        found = self._read_attempt(draft_id)
        if found is None or found.state != "prepared":
            return found
        try:
            with self.publish_lock():
                found = self._read_attempt(draft_id)
                if found is not None and found.state == "prepared":
                    self.release(found)
                    return None
                return found
        except DraftConflictError:
            return found

    def _read_attempt(self, draft_id: str) -> Attempt | None:
        with self.connect() as db:
            row = db.execute("SELECT body FROM attempts WHERE id=?", (draft_id,)).fetchone()
        return Attempt.model_validate_json(row[0]) if row else None

    def save_attempt(self, attempt: Attempt) -> None:
        """Persist observations without allowing an intent or approved baseline to disappear."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM attempts WHERE id=?", (attempt.draft_id,)).fetchone()
            if row:
                previous = Attempt.model_validate_json(row[0])
                if previous.publish_intent_at and (
                    attempt.publish_intent_at != previous.publish_intent_at
                    or attempt.final_approval != previous.final_approval
                    or attempt.snapshot != previous.snapshot
                ):
                    raise DraftConflictError("Consumed publication evidence is immutable.")
                if previous.live_observed and not attempt.live_observed:
                    raise DraftConflictError("A live observation cannot reopen publication.")
                if (
                    previous.state == "historical_unverified"
                    and attempt.state != "historical_unverified"
                ):
                    raise DraftConflictError("A historical attempt cannot reopen publication.")
            db.execute(
                "INSERT OR REPLACE INTO attempts VALUES (?, ?)",
                (attempt.draft_id, attempt.model_dump_json()),
            )

    def release(self, attempt: Attempt) -> None:
        """Only a demonstrably unsent or definitely rejected create may release its slot."""
        if attempt.state != "prepared" or attempt.remote_id or attempt.publish_intent_at:
            raise DraftConflictError("A sent attempt cannot be released.")
        with self.connect() as db:
            db.execute("DELETE FROM attempts WHERE id=?", (attempt.draft_id,))
            db.execute("DELETE FROM reviews WHERE id=?", (attempt.draft_id,))

    def invalidate_review(self, draft_id: str) -> None:
        """Revocation is durable even if later evidence happens to revert."""
        with self.connect() as db:
            db.execute("DELETE FROM reviews WHERE id=?", (draft_id,))

    def review(
        self,
        draft: Draft,
        token: str,
        fingerprint: str,
        payload: dict,
        *,
        purpose: str,
        snapshot: ProcessedSnapshot | None = None,
    ) -> None:
        """Persist a purpose-bound challenge, never remote-write permission."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT revision FROM drafts WHERE id=?", (draft.id,)).fetchone()
            if not current or current[0] != draft.revision:
                raise DraftConflictError("Draft changed during review. Reload.")
            db.execute(
                "INSERT OR REPLACE INTO reviews "
                "(id,token,revision,fingerprint,payload,purpose,snapshot_id,manifest_digest,contract) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    draft.id,
                    token,
                    draft.revision,
                    fingerprint,
                    json.dumps(payload),
                    purpose,
                    snapshot.id if snapshot else None,
                    snapshot.digest if snapshot else None,
                    CONTRACT,
                ),
            )

    @staticmethod
    def _challenge(
        db: sqlite3.Connection, draft: Draft, token: str, fingerprint: str, purpose: str
    ) -> sqlite3.Row:
        review = db.execute("SELECT * FROM reviews WHERE id=?", (draft.id,)).fetchone()
        current = db.execute("SELECT revision FROM drafts WHERE id=?", (draft.id,)).fetchone()
        if (
            not review
            or not token
            or review["token"] != token
            or review["fingerprint"] != fingerprint
            or review["purpose"] != purpose
            or review["revision"] != draft.revision
            or not current
            or review["contract"] != CONTRACT
            or current[0] != draft.revision
        ):
            raise DraftConflictError("Approval is missing, stale or for a different purpose.")
        return review

    def claim(
        self, draft: Draft, token: str, fingerprint: str, preparation: PreparationGrant
    ) -> Attempt:
        """Preparation grant and the sole create reservation are one transaction."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            review = self._challenge(db, draft, token, fingerprint, "prepare")
            if db.execute("SELECT 1 FROM attempts WHERE id=?", (draft.id,)).fetchone():
                raise DraftConflictError("Attempt already exists; use read-only status.")
            attempt = Attempt(
                draft_id=draft.id,
                revision=draft.revision,
                correlation=f"synthshop-{draft.id}",
                fingerprint=fingerprint,
                approved_payload=json.loads(review["payload"]),
                preparation=preparation,
            )
            db.execute("INSERT INTO attempts VALUES (?, ?)", (draft.id, attempt.model_dump_json()))
            db.execute("DELETE FROM reviews WHERE id=?", (draft.id,))
            return attempt

    def claim_publish(self, draft: Draft, token: str, expected: Attempt) -> Attempt:
        """Consume final approval and persist intent atomically BEFORE at most one PUT."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            review = self._challenge(db, draft, token, expected.fingerprint, "publish")
            row = db.execute("SELECT body FROM attempts WHERE id=?", (draft.id,)).fetchone()
            attempt = Attempt.model_validate_json(row[0]) if row else None
            if (
                attempt != expected
                or attempt.state != "review_ready"
                or attempt.publish_intent_at
                or attempt.live_observed
                or attempt.contract != CONTRACT
                or not attempt.snapshot
                or review["snapshot_id"] != attempt.snapshot.id
                or review["manifest_digest"] != attempt.snapshot.digest
            ):
                raise DraftConflictError("Processed approval changed or was already consumed.")
            attempt.snapshot.verify()
            attempt.final_approval = FinalApproval(
                snapshot_id=attempt.snapshot.id,
                manifest_digest=attempt.snapshot.digest,
            )
            attempt.publish_intent_at = now()
            attempt.state = "publishing"
            db.execute(
                "UPDATE attempts SET body=? WHERE id=?", (attempt.model_dump_json(), draft.id)
            )
            db.execute("DELETE FROM reviews WHERE id=?", (draft.id,))
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
                    owner_fields=[
                        "make",
                        "model",
                        "variant",
                        "description",
                        "title",
                        "condition",
                        "price",
                    ],
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
