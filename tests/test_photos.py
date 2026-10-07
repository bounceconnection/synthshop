"""Local photo intake, including phone JPEGs that carry an HDR gain map."""

import hashlib
import io
import struct
from unittest.mock import patch

import pytest
from PIL import Image

from tests.test_cli import LOCAL, browser

XMP = (
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
    '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    "<rdf:Description {}/></rdf:RDF></x:xmpmeta>"
)
APPLE_GAIN_MAP = XMP.format(
    'xmlns:apdi="http://ns.apple.com/pixeldatainfo/1.0/" '
    'xmlns:HDRGainMap="http://ns.apple.com/HDRGainMap/1.0/" '
    'apdi:AuxiliaryImageType="urn:com:apple:photo:2020:aux:hdrgainmap" '
    'HDRGainMap:HDRGainMapVersion="65536"'
).encode()
ADOBE_GAIN_MAP = XMP.format(
    'xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" '
    'hdrgm:Version="1.0" hdrgm:GainMapMax="2.0"'
).encode()
UNRELATED = XMP.format(
    'xmlns:dc="http://purl.org/dc/elements/1.1/" dc:format="image/jpeg"'
).encode()


def split_primary() -> Image.Image:
    """Red left half, blue right half, so orientation and frame choice are both visible."""
    image = Image.new("RGB", (120, 60), "blue")
    image.paste("red", (0, 0, 60, 60))
    return image


def multi_picture(*auxiliary: Image.Image, xmp: bytes | None = APPLE_GAIN_MAP) -> bytes:
    """Encode a real JPEG/MPF container: an EXIF-rotated primary plus auxiliary JPEGs."""
    for frame in auxiliary:
        frame.encoderinfo = {"xmp": xmp} if xmp else {}
    exif = Image.Exif()
    exif[274] = 6  # Displayed after a 90° clockwise rotation.
    stream = io.BytesIO()
    split_primary().save(stream, "MPO", save_all=True, append_images=list(auxiliary), exif=exif)
    return stream.getvalue()


def gain_map(size=(60, 30)) -> Image.Image:
    return Image.new("L", size, 128)


def primary_entry(content: bytes) -> dict:
    with Image.open(io.BytesIO(content)) as image:
        return image.mpinfo[0xB002][0]


GAIN_MAP_JPEG = multi_picture(gain_map())
PRIMARY_ONLY = GAIN_MAP_JPEG[: primary_entry(GAIN_MAP_JPEG)["Size"]]


def with_second_mp_type(content: bytes, mp_type: int) -> bytes:
    """Relabel the auxiliary MP entry, e.g. as one half of a stereo pair."""
    first = primary_entry(content)
    entry = content.index(struct.pack("<LLL", 0x030000, first["Size"], first["DataOffset"])) + 16
    return content[:entry] + struct.pack("<L", mp_type) + content[entry + 4 :]


def assert_red_top_blue_bottom(path):
    with Image.open(path) as stored:
        assert stored.format == "JPEG"
        assert stored.mode == "RGB"
        assert stored.size == (60, 120)
        assert not stored.getexif()
        assert not stored.info.get("mpinfo")
        top, bottom = stored.getpixel((30, 15)), stored.getpixel((30, 105))
    assert top[0] > 200 and top[2] < 60
    assert bottom[2] > 200 and bottom[0] < 60


@pytest.mark.parametrize("xmp", [APPLE_GAIN_MAP, ADOBE_GAIN_MAP], ids=["apple", "adobe"])
def test_hdr_gain_map_jpeg_uses_oriented_primary_and_keeps_original(application, xmp):
    content = multi_picture(gain_map(), xmp=xmp)
    with Image.open(io.BytesIO(content)) as decoded:
        assert (decoded.format, decoded.n_frames) == ("MPO", 2)

    photo = application.upload([content]).photos[0]

    assert photo.original_format == "JPEG"
    assert (photo.width, photo.height) == (60, 120)
    derivative = application.photos.path(photo.id)
    assert hashlib.sha256(derivative.read_bytes()).hexdigest() == photo.digest
    assert_red_top_blue_bottom(derivative)
    assert application.photos.path(photo.id, original=True).read_bytes() == content


def test_hdr_gain_map_jpeg_uploads_through_the_web_form(application):
    client, csrf = browser(application)
    response = client.post(
        "/drafts",
        data={"csrf": csrf},
        files={"photos": ("IMG_0001.jpeg", GAIN_MAP_JPEG, "image/jpeg")},
        headers={"Origin": LOCAL},
    )
    assert response.status_code == 200
    (item,) = application.store.list_all()
    assert client.get(f"/photos/{item.photos[0].id}").headers["content-type"] == "image/jpeg"
    assert_red_top_blue_bottom(application.photos.path(item.photos[0].id))
    assert application.photos.path(item.photos[0].id, original=True).read_bytes() == GAIN_MAP_JPEG


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(multi_picture(Image.new("RGB", (120, 60)), xmp=None), id="stereo-like-pair"),
        pytest.param(multi_picture(gain_map(), xmp=UNRELATED), id="unrecognized-auxiliary"),
        pytest.param(multi_picture(gain_map(), gain_map()), id="extra-picture"),
        pytest.param(with_second_mp_type(GAIN_MAP_JPEG, 0x020002), id="disparity"),
        pytest.param(multi_picture(gain_map((240, 60))), id="gain-map-larger-than-primary"),
        pytest.param(GAIN_MAP_JPEG[:-40], id="truncated-gain-map"),
        pytest.param(GAIN_MAP_JPEG[:700], id="truncated-container"),
        pytest.param(PRIMARY_ONLY, id="gain-map-missing"),
        pytest.param(PRIMARY_ONLY + b"\0" * 64, id="gain-map-not-jpeg"),
    ],
)
def test_other_multi_picture_and_malformed_containers_rejected(application, content):
    with pytest.raises(ValueError, match="Multi-picture JPEGs|Corrupt"):
        application.upload([content])
    assert application.store.list_all() == []
    assert not any(application.photos.root.iterdir())


def test_hdr_gain_map_jpeg_keeps_the_pixel_limit(application):
    with (
        patch("synthshop.core.photos.MAX_PIXELS", 120 * 60 - 1),
        pytest.raises(ValueError, match="megapixels"),
    ):
        application.upload([GAIN_MAP_JPEG])
    assert not any(application.photos.root.iterdir())


@pytest.mark.parametrize("kind", ["JPEG", "PNG", "WEBP", "GIF"])
def test_still_formats_remain_accepted(application, kind):
    stream = io.BytesIO()
    split_primary().convert("P" if kind == "GIF" else "RGB").save(stream, kind)
    photo = application.upload([stream.getvalue()]).photos[0]
    assert photo.original_format == kind
    assert (photo.width, photo.height) == (120, 60)


def test_animation_remains_rejected(application):
    stream = io.BytesIO()
    frames = [Image.new("RGB", (40, 40), color) for color in ("red", "blue")]
    frames[0].save(stream, "GIF", save_all=True, append_images=frames[1:])
    with pytest.raises(ValueError, match="Animated"):
        application.upload([stream.getvalue()])
    assert application.store.list_all() == []
