"""Tests for custom/aftermarket panel detection."""

from unittest.mock import MagicMock, patch

import pytest

from synthshop.cli.commands.identify import _check_custom_panel
from synthshop.integrations.claude_vision import (
    CustomPanelResult,
    SynthIdentification,
    detect_custom_panel,
)


@pytest.fixture
def sample_result():
    """A basic identification result for testing panel detection."""
    return SynthIdentification(
        make="Mungo Enterprises",
        model="d0",
        category="synthesizers",
        description="Dual Channel Delay",
        features=["Dual Delay", "12HP Eurorack module", "Discontinued — increasingly rare"],
        condition="Very Good",
        condition_notes="Module appears clean.",
        price_low=180.0,
        price_high=250.0,
        confidence="high",
        notes="Discontinued. Boutique Australian manufacturer.",
    )


@pytest.fixture
def jpeg_image(tmp_path):
    """Create a minimal JPEG file for testing."""
    from PIL import Image

    img = Image.new("RGB", (10, 10), color=(255, 0, 0))
    path = tmp_path / "module.jpg"
    img.save(path, format="JPEG")
    return path


class TestCheckCustomPanel:
    """Test the _check_custom_panel orchestration function."""

    @patch("synthshop.cli.commands.identify.detect_custom_panel")
    def test_custom_panel_detected(
        self, mock_detect, sample_result, jpeg_image,
    ):
        """Custom panel is noted without attributing any maker."""
        mock_detect.return_value = CustomPanelResult(
            is_custom=True,
            confidence="high",
            description="Black aluminum panel with gold organic shapes",
        )

        result = _check_custom_panel(
            sample_result, [jpeg_image],
            "https://modulargrid.net/img/modcache/123.f.jpg",
        )

        assert result.custom_panel is True
        assert result.custom_panel_maker is None
        assert "Custom/aftermarket panel" in result.features
        assert "Custom/aftermarket panel" in result.notes
        # Should NEVER attribute any specific maker
        assert "Grayscale" not in result.notes
        assert "Magpie" not in result.notes

    @patch("synthshop.cli.commands.identify.detect_custom_panel")
    def test_stock_panel_preserves_result(
        self, mock_detect, sample_result, jpeg_image,
    ):
        mock_detect.return_value = CustomPanelResult(
            is_custom=False,
            confidence="high",
            description="",
        )

        original_features = list(sample_result.features)
        original_notes = sample_result.notes

        result = _check_custom_panel(
            sample_result, [jpeg_image],
            "https://modulargrid.net/img/modcache/123.f.jpg",
        )

        assert result.custom_panel is False
        assert result.custom_panel_maker is None
        assert result.features == original_features
        assert result.notes == original_notes

    @patch("synthshop.cli.commands.identify.detect_custom_panel")
    def test_low_confidence_skipped(
        self, mock_detect, sample_result, jpeg_image,
    ):
        """Low confidence results are treated as stock panels."""
        mock_detect.return_value = CustomPanelResult(
            is_custom=True,
            confidence="low",
            description="Might be custom but unclear.",
        )

        result = _check_custom_panel(
            sample_result, [jpeg_image],
            "https://modulargrid.net/img/modcache/123.f.jpg",
        )

        assert result.custom_panel is False

    @patch("synthshop.cli.commands.identify.detect_custom_panel")
    def test_api_error_preserves_result(
        self, mock_detect, sample_result, jpeg_image,
    ):
        """API errors are caught and the result is returned unchanged."""
        mock_detect.side_effect = RuntimeError("API error")

        original_features = list(sample_result.features)
        result = _check_custom_panel(
            sample_result, [jpeg_image],
            "https://modulargrid.net/img/modcache/123.f.jpg",
        )

        assert result.custom_panel is False
        assert result.features == original_features


class TestDetectCustomPanel:
    """Test the Claude Vision panel comparison call."""

    @patch("synthshop.integrations.claude_vision._download_image_as_base64")
    @patch("synthshop.integrations.claude_vision.anthropic.Anthropic")
    def test_detects_custom_panel(self, mock_anthropic_cls, mock_download, jpeg_image):
        mock_download.return_value = "base64stockimage"

        mock_tool_block = MagicMock()
        mock_tool_block.type = "tool_use"
        mock_tool_block.name = "detect_custom_panel"
        mock_tool_block.input = {
            "is_custom": True,
            "confidence": "high",
            "description": "Black panel with gold organic shapes, differs from stock",
        }

        mock_response = MagicMock()
        mock_response.content = [mock_tool_block]
        mock_anthropic_cls.return_value.messages.create.return_value = mock_response

        result = detect_custom_panel(
            [jpeg_image],
            "https://modulargrid.net/img/modcache/123.f.jpg",
            api_key="test-key",
        )

        assert result.is_custom is True
        assert result.confidence == "high"

    @patch("synthshop.integrations.claude_vision._download_image_as_base64")
    @patch("synthshop.integrations.claude_vision.anthropic.Anthropic")
    def test_detects_stock_panel(self, mock_anthropic_cls, mock_download, jpeg_image):
        mock_download.return_value = "base64stockimage"

        mock_tool_block = MagicMock()
        mock_tool_block.type = "tool_use"
        mock_tool_block.name = "detect_custom_panel"
        mock_tool_block.input = {
            "is_custom": False,
            "confidence": "high",
            "description": "",
        }

        mock_response = MagicMock()
        mock_response.content = [mock_tool_block]
        mock_anthropic_cls.return_value.messages.create.return_value = mock_response

        result = detect_custom_panel(
            [jpeg_image],
            "https://modulargrid.net/img/modcache/123.f.jpg",
            api_key="test-key",
        )

        assert result.is_custom is False

    @patch("synthshop.integrations.claude_vision._download_image_as_base64")
    @patch("synthshop.integrations.claude_vision.anthropic.Anthropic")
    def test_still_works_when_stock_image_fails(
        self, mock_anthropic_cls, mock_download, jpeg_image,
    ):
        """If stock image can't be downloaded, still sends user photos."""
        mock_download.return_value = None

        mock_tool_block = MagicMock()
        mock_tool_block.type = "tool_use"
        mock_tool_block.name = "detect_custom_panel"
        mock_tool_block.input = {
            "is_custom": False,
            "confidence": "low",
            "description": "",
        }

        mock_response = MagicMock()
        mock_response.content = [mock_tool_block]
        mock_anthropic_cls.return_value.messages.create.return_value = mock_response

        result = detect_custom_panel(
            [jpeg_image],
            "https://modulargrid.net/img/modcache/123.f.jpg",
            api_key="test-key",
        )

        assert isinstance(result, CustomPanelResult)

        # Verify stock image block was NOT included
        call_kwargs = mock_anthropic_cls.return_value.messages.create.call_args
        content_blocks = call_kwargs.kwargs["messages"][0]["content"]
        image_blocks = [b for b in content_blocks if b.get("type") == "image"]
        assert len(image_blocks) == 1  # Only user photo
