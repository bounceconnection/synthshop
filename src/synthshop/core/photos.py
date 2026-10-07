"""Decode by content, orient, and retain private originals plus stripped JPEGs."""

import hashlib
import io
import warnings
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from synthshop.core.models import Photo

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_PHOTOS = 25
FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
GAIN_MAP_MARKERS = (
    b"urn:com:apple:photo:2020:aux:hdrgainmap",  # Apple auxiliary image type
    b"http://ns.adobe.com/hdr-gain-map/1.0/",  # Adobe/Ultra HDR gain-map namespace
)


def _require_hdr_gain_map(content: bytes) -> None:
    """Accept a multi-picture JPEG only as one primary photo plus its HDR gain map.

    Pillow reports such phone JPEGs as MPO. The gain map is decoded so a corrupt
    container is rejected, but only the primary becomes the SDR derivative; the
    retained original keeps both. Stereo pairs, panoramas and extra pictures fail.
    """
    with Image.open(io.BytesIO(content)) as container:
        types = [entry["Attribute"]["MPType"] for entry in container.mpinfo[0xB002]]
        if container.n_frames == 2 and types == ["Baseline MP Primary Image", "Undefined"]:
            width, height = container.size
            try:
                container.seek(1)
            except (SyntaxError, ValueError) as exc:  # Pillow: missing or non-JPEG picture
                raise UnidentifiedImageError("Unreadable gain-map picture") from exc
            xmp = [
                data
                for app, data in container.applist
                if app == "APP1" and data.startswith(XMP_HEADER)
            ]
            if (
                len(xmp) == 1
                and any(marker in xmp[0] for marker in GAIN_MAP_MARKERS)
                and container.width <= width
                and container.height <= height
            ):
                container.load()
                return
    raise ValueError("Multi-picture JPEGs are supported only as one photo plus an HDR gain map.")


class PhotoLibrary:
    """Paths are derived solely from validated random IDs, never upload filenames."""

    def __init__(self, root: Path):
        self.root = root / "photos"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def add(self, content: bytes) -> Photo:
        """Validate the full decode, rejecting animation and decompression bombs."""
        if not content or len(content) > MAX_BYTES:
            raise ValueError("Each photo must be nonempty and at most 20 MiB.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as original:
                    actual_format = original.format
                    if actual_format not in FORMATS and actual_format != "MPO":
                        raise ValueError("Supported decoded formats: JPEG, PNG, WebP, still GIF.")
                    if original.width * original.height > MAX_PIXELS:
                        raise ValueError("Photo exceeds 40 megapixels. Resize before uploading.")
                    if actual_format == "MPO":
                        _require_hdr_gain_map(content)
                        actual_format = "JPEG"
                    elif getattr(original, "n_frames", 1) != 1:
                        raise ValueError("Animated images are unsupported. Upload a still photo.")
                    original.load()
                    oriented = ImageOps.exif_transpose(original)
                    # Composite transparency onto white instead of inventing a black background.
                    rgba = oriented.convert("RGBA")
                    image = Image.new("RGB", rgba.size, "white")
                    image.paste(rgba, mask=rgba.getchannel("A"))
                    image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                    output = io.BytesIO()
                    image.save(output, "JPEG", quality=90)
        except (
            UnidentifiedImageError,
            OSError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise ValueError(
                "Corrupt, unsupported, or oversized image; upload a valid still photo."
            ) from exc
        derivative = output.getvalue()
        photo = Photo(
            id=uuid4().hex,
            digest=hashlib.sha256(derivative).hexdigest(),
            original_format=actual_format,
            width=image.width,
            height=image.height,
        )
        self.path(photo.id, original=True).write_bytes(content)
        self.path(photo.id).write_bytes(derivative)
        return photo

    def path(self, photo_id: str, *, original: bool = False) -> Path:
        """Constrain all reads to the managed library."""
        if len(photo_id) != 32 or any(char not in "0123456789abcdef" for char in photo_id):
            raise ValueError("Invalid photo identity")
        return self.root / f"{photo_id}.{'original' if original else 'jpg'}"

    def verify(self, photos: list[Photo]) -> None:
        """Detect deletion or tampering after review, before staging anything."""
        if not 1 <= len(photos) <= MAX_PHOTOS:
            raise ValueError("Review between 1 and 25 current-item photos before publishing.")
        for photo in photos:
            if hashlib.sha256(self.path(photo.id).read_bytes()).hexdigest() != photo.digest:
                raise ValueError("A reviewed photo changed on disk. Upload and review it again.")
