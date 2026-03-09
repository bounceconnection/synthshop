"""Tests for Claude Vision synth identification (mocked API)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from synthshop.integrations.claude_vision import (
    SynthIdentification,
    _build_content_blocks,
    _encode_image,
    identify_from_photos,
)


@pytest.fixture
def jpeg_image(tmp_path) -> Path:
    """Create a minimal valid JPEG file for testing."""
    # Minimal JPEG: SOI marker + EOI marker
    img = tmp_path / "synth.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100 + b"\xff\xd9")
    return img


@pytest.fixture
def png_image(tmp_path) -> Path:
    """Create a minimal valid PNG file for testing."""
    img = tmp_path / "synth.png"
    # PNG magic bytes + minimal data
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    return img


@pytest.fixture
def mock_tool_response():
    """A mock Anthropic API response with tool use output."""
    tool_input = {
        "make": "Roland",
        "model": "Juno-106",
        "year": 1984,
        "variant": None,
        "category": "synthesizers",
        "description": (
            "The Roland Juno-106 is one of the most beloved analog polysynths ever made. "
            "Its warm, lush pads and simple interface make it a studio staple. "
            "This unit appears to be in excellent condition with all voice chips working."
        ),
        "features": [
            "6-voice analog polyphonic",
            "DCO-based oscillators",
            "Classic chorus effect",
            "61 full-size keys",
            "MIDI equipped",
            "128 patch memory",
        ],
        "condition": "Excellent",
        "condition_notes": "Minor cosmetic wear on case edges, all keys and sliders functional.",
        "price_low": 1100.0,
        "price_high": 1600.0,
        "confidence": "high",
        "notes": "Check all 6 voice chips — the 80017A chips are known to fail on these units.",
    }

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "identify_synth"
    tool_block.input = tool_input

    response = MagicMock()
    response.content = [tool_block]
    return response


# --- _encode_image tests ---


class TestEncodeImage:
    def test_encodes_jpeg(self, jpeg_image):
        data, media_type = _encode_image(jpeg_image)
        assert media_type == "image/jpeg"
        assert len(data) > 0

    def test_encodes_png(self, png_image):
        data, media_type = _encode_image(png_image)
        assert media_type == "image/png"
        assert len(data) > 0

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Image not found"):
            _encode_image(tmp_path / "nope.jpg")

    def test_unsupported_type_raises(self, tmp_path):
        txt = tmp_path / "notes.txt"
        txt.write_text("not an image")
        with pytest.raises(ValueError, match="Unsupported image type"):
            _encode_image(txt)


# --- _build_content_blocks tests ---


class TestBuildContentBlocks:
    def test_single_image(self, jpeg_image):
        blocks = _build_content_blocks([jpeg_image])
        assert len(blocks) == 2
        assert blocks[0]["type"] == "image"
        assert blocks[0]["source"]["media_type"] == "image/jpeg"
        assert blocks[1]["type"] == "text"

    def test_multiple_images(self, jpeg_image, png_image):
        blocks = _build_content_blocks([jpeg_image, png_image])
        assert len(blocks) == 3
        assert blocks[0]["type"] == "image"
        assert blocks[1]["type"] == "image"
        assert blocks[2]["type"] == "text"


# --- SynthIdentification model tests ---


class TestSynthIdentification:
    def test_from_dict(self):
        data = {
            "make": "Moog",
            "model": "Subsequent 37",
            "year": 2017,
            "category": "synthesizers",
            "description": "A powerful mono/duo synth.",
            "features": ["Analog", "Aftertouch"],
            "condition": "Very Good",
            "price_low": 1000.0,
            "price_high": 1400.0,
            "confidence": "high",
        }
        result = SynthIdentification.model_validate(data)
        assert result.make == "Moog"
        assert result.variant is None
        assert result.condition_notes == ""

    def test_minimal_fields(self):
        data = {
            "make": "Korg",
            "model": "Minilogue",
            "category": "synthesizers",
            "description": "Great entry-level polysynth.",
            "features": ["4-voice", "Analog"],
            "condition": "Good",
            "price_low": 300.0,
            "price_high": 450.0,
            "confidence": "medium",
        }
        result = SynthIdentification.model_validate(data)
        assert result.year is None
        assert result.notes == ""


# --- identify_from_photos tests ---


class TestIdentifyFromPhotos:
    def test_no_images_raises(self):
        with pytest.raises(ValueError, match="At least one image"):
            identify_from_photos([])

    @patch("synthshop.integrations.claude_vision.anthropic.Anthropic")
    def test_successful_identification(self, mock_anthropic_cls, jpeg_image, mock_tool_response):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_tool_response
        mock_anthropic_cls.return_value = mock_client

        result = identify_from_photos([jpeg_image], api_key="sk-ant-test")

        assert isinstance(result, SynthIdentification)
        assert result.make == "Roland"
        assert result.model == "Juno-106"
        assert result.year == 1984
        assert result.category == "synthesizers"
        assert result.confidence == "high"
        assert result.price_low == 1100.0
        assert result.price_high == 1600.0
        assert len(result.features) == 6

        # Verify the API was called correctly
        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert call_kwargs["tool_choice"] == {"type": "tool", "name": "identify_synth"}
        assert len(call_kwargs["tools"]) == 1
        assert call_kwargs["tools"][0]["name"] == "identify_synth"

    @patch("synthshop.integrations.claude_vision.anthropic.Anthropic")
    def test_multiple_images(self, mock_anthropic_cls, jpeg_image, png_image, mock_tool_response):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_tool_response
        mock_anthropic_cls.return_value = mock_client

        result = identify_from_photos([jpeg_image, png_image], api_key="sk-ant-test")

        assert result.make == "Roland"
        # Check both images were included in the message
        call_kwargs = mock_client.messages.create.call_args[1]
        content = call_kwargs["messages"][0]["content"]
        image_blocks = [b for b in content if b["type"] == "image"]
        assert len(image_blocks) == 2

    @patch("synthshop.integrations.claude_vision.anthropic.Anthropic")
    def test_no_tool_use_raises(self, mock_anthropic_cls, jpeg_image):
        """If Claude responds with text instead of tool use, raise RuntimeError."""
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "I can see a synthesizer..."

        response = MagicMock()
        response.content = [text_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = response
        mock_anthropic_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="did not return"):
            identify_from_photos([jpeg_image], api_key="sk-ant-test")

    @patch("synthshop.integrations.claude_vision.anthropic.Anthropic")
    def test_custom_model(self, mock_anthropic_cls, jpeg_image, mock_tool_response):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_tool_response
        mock_anthropic_cls.return_value = mock_client

        identify_from_photos(
            [jpeg_image], api_key="sk-ant-test", model="claude-opus-4-6-20250610"
        )

        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-opus-4-6-20250610"
