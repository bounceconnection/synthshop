"""Claude Vision integration for synth identification from photos."""

import base64
import io
import mimetypes
from pathlib import Path

import anthropic
from PIL import Image
from pydantic import BaseModel, Field

from synthshop.cli.prompts import IDENTIFY_SYSTEM, IDENTIFY_USER
from synthshop.core.config import settings

# Tool schema for structured output — defines what Claude returns
IDENTIFY_TOOL = {
    "name": "identify_synth",
    "description": (
        "Return structured identification results for the music equipment in the photos."
    ),
    "input_schema": {
        "type": "object",
        "required": [
            "make",
            "model",
            "category",
            "description",
            "features",
            "condition",
            "price_low",
            "price_high",
            "confidence",
        ],
        "properties": {
            "make": {
                "type": "string",
                "description": "Manufacturer name",
            },
            "model": {
                "type": "string",
                "description": "Exact model name",
            },
            "year": {
                "type": ["integer", "null"],
                "description": "Approximate production year, or null if uncertain",
            },
            "variant": {
                "type": ["string", "null"],
                "description": "Specific variant/revision, or null if standard",
            },
            "category": {
                "type": "string",
                "enum": [
                    "synthesizers",
                    "drum-machines",
                    "samplers",
                    "effects",
                    "keyboards",
                    "studio-gear",
                    "other",
                ],
            },
            "description": {
                "type": "string",
                "description": "2-3 sentence listing description",
            },
            "features": {
                "type": "array",
                "items": {"type": "string"},
                "description": "4-8 key features/selling points",
            },
            "condition": {
                "type": "string",
                "enum": ["Mint", "Excellent", "Very Good", "Good", "Fair", "Poor"],
            },
            "condition_notes": {
                "type": "string",
                "description": "Notes on visible wear or damage",
                "default": "",
            },
            "price_low": {
                "type": "number",
                "description": "Low end of estimated market value in USD",
            },
            "price_high": {
                "type": "number",
                "description": "High end of estimated market value in USD",
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "notes": {
                "type": "string",
                "description": "Additional observations or things to verify",
                "default": "",
            },
        },
    },
}


class SynthIdentification(BaseModel):
    """Structured result from Claude Vision synth identification."""

    make: str
    model: str
    year: int | None = None
    variant: str | None = None
    category: str
    description: str
    features: list[str] = Field(default_factory=list)
    condition: str
    condition_notes: str = ""
    price_low: float
    price_high: float
    confidence: str  # "high", "medium", "low"
    notes: str = ""


def _encode_image(path: Path) -> tuple[str, str]:
    """Read an image file and return (base64_data, media_type).

    Raises FileNotFoundError if the file doesn't exist, ValueError if
    the file type isn't a supported image format.
    """
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")

    mime_type, _ = mimetypes.guess_type(str(path))
    supported = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    if mime_type not in supported:
        raise ValueError(
            f"Unsupported image type '{mime_type}' for {path.name}. "
            f"Supported: JPEG, PNG, GIF, WebP"
        )

    image_bytes = path.read_bytes()

    # Claude API limit is 5MB for the base64 string. Base64 inflates size
    # by ~4/3, so the raw file limit is ~3.75MB to stay under 5MB encoded.
    max_raw_bytes = (5 * 1024 * 1024 * 3) // 4  # ~3.75MB
    if len(image_bytes) > max_raw_bytes:
        image_bytes = _resize_image(image_bytes, max_raw_bytes)
        mime_type = "image/jpeg"

    data = base64.standard_b64encode(image_bytes).decode("utf-8")
    return data, mime_type


def _resize_image(image_bytes: bytes, max_bytes: int) -> bytes:
    """Resize an image to fit under max_bytes while preserving aspect ratio."""
    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGB")  # handle RGBA, palette, etc.

    # Iteratively scale down until under the limit
    quality = 85
    scale = 0.9
    while True:
        new_size = (int(img.width * scale), int(img.height * scale))
        resized = img.resize(new_size, Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="JPEG", quality=quality)
        if buf.tell() <= max_bytes:
            return buf.getvalue()
        scale *= 0.8


def _build_content_blocks(image_paths: list[Path]) -> list[dict]:
    """Build the content array with image blocks followed by the text prompt."""
    blocks: list[dict] = []
    for path in image_paths:
        data, media_type = _encode_image(path)
        blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": data,
            },
        })
    blocks.append({"type": "text", "text": IDENTIFY_USER})
    return blocks


def identify_from_photos(
    image_paths: list[Path],
    *,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-20250514",
) -> SynthIdentification:
    """Send photos to Claude Vision and get a structured synth identification.

    Args:
        image_paths: Paths to image files (JPEG, PNG, GIF, or WebP).
        api_key: Anthropic API key. Falls back to settings if not provided.
        model: Claude model to use. Defaults to Sonnet for speed/cost balance.

    Returns:
        SynthIdentification with all identified fields.

    Raises:
        FileNotFoundError: If any image path doesn't exist.
        ValueError: If no images provided or unsupported image type.
        anthropic.APIError: On API failures.
        RuntimeError: If Claude doesn't return a tool use response.
    """
    if not image_paths:
        raise ValueError("At least one image is required.")

    key = api_key or settings.require_anthropic()
    client = anthropic.Anthropic(api_key=key)

    content = _build_content_blocks(image_paths)

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=IDENTIFY_SYSTEM,
        tools=[IDENTIFY_TOOL],
        tool_choice={"type": "tool", "name": "identify_synth"},
        messages=[{"role": "user", "content": content}],
    )

    # Extract the tool use block from the response
    for block in response.content:
        if block.type == "tool_use" and block.name == "identify_synth":
            return SynthIdentification.model_validate(block.input)

    raise RuntimeError(
        "Claude did not return an identify_synth tool call. "
        f"Response: {response.content}"
    )
