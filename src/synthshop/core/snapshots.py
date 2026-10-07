"""Private, bounded processed entities. Validation never re-encodes provider bytes."""

import hashlib
import io
import os
import re
import warnings
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from synthshop.core.models import ProcessedSnapshot, Representation
from synthshop.core.photos import MAX_BYTES, MAX_PIXELS

MEDIA_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def decoded_metadata(content: bytes, media_type: str) -> dict:
    """Require static, fully decodable supported bytes and truthful Content-Type."""
    if not content or len(content) > MAX_BYTES:
        raise ValueError("Processed photo is empty or exceeds 20 MiB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if MEDIA_TYPES.get(image.format) != media_type:
                    raise ValueError("Processed photo format and media type are unsupported.")
                if image.width * image.height > MAX_PIXELS or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Processed photo must be static and at most 40 megapixels.")
                image.load()
                return {"format": image.format, "width": image.width, "height": image.height}
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("Processed photo cannot be decoded safely.") from exc


class SnapshotLibrary:
    """Generated local blob IDs only; no source locators or derivative pipeline."""

    def __init__(self, root: Path):
        self.root = root / "processed"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)

    def path(self, blob_id: str) -> Path:
        """Reject traversal and symlink substitution inside private storage."""
        if not re.fullmatch(r"[a-f0-9]{32}", blob_id):
            raise ValueError("Invalid snapshot blob identity")
        path = self.root / blob_id
        if path.is_symlink() or path.resolve().parent != self.root.resolve():
            raise ValueError("Invalid snapshot storage path")
        return path

    def put(self, entry: Representation, content: bytes) -> None:
        """Write completely before a manifest can expose this blob."""
        if len(content) != entry.byte_count or hashlib.sha256(content).hexdigest() != entry.digest:
            raise ValueError("Processed photo integrity check failed.")
        path = self.path(entry.blob_id)
        temporary = path.with_suffix(".partial")
        try:
            with temporary.open("xb") as handle:
                os.chmod(temporary, 0o600)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, entry: Representation) -> bytes:
        """Return the very bytes whose integrity was checked, avoiding a serving race."""
        with self.path(entry.blob_id).open("rb") as handle:
            content = handle.read(MAX_BYTES + 1)
        if len(content) != entry.byte_count or hashlib.sha256(content).hexdigest() != entry.digest:
            raise ValueError("Cached processed photo changed; no approval is usable.")
        if decoded_metadata(content, entry.media_type) != {
            "format": entry.format,
            "width": entry.width,
            "height": entry.height,
        }:
            raise ValueError("Cached processed photo metadata changed.")
        return content

    def verify(self, snapshot: ProcessedSnapshot) -> None:
        """Every reviewable entity, including independent cover, must be intact."""
        snapshot.verify()
        for entry in [*snapshot.gallery, snapshot.cover]:
            self.read(entry)

    def remove(self, snapshot: ProcessedSnapshot) -> None:
        """Only caller-owned, replaced unapproved captures may be removed."""
        for entry in [*snapshot.gallery, snapshot.cover]:
            self.path(entry.blob_id).unlink(missing_ok=True)
