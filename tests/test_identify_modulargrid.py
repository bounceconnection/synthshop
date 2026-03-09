"""Tests for the identify command's ModularGrid integration.

Verifies that when ModularGrid finds a match, the identify command:
- Corrects the manufacturer and model
- Replaces Claude's description with ModularGrid's
- Replaces Claude's features with ModularGrid's
- Adds discontinued status and HP info
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from synthshop.cli.commands.identify import _verify_with_modulargrid
from synthshop.integrations.claude_vision import SynthIdentification


@pytest.fixture
def claude_chainsaw_wrong():
    """Claude's wrong identification of Chainsaw — manufacturer misidentified."""
    return SynthIdentification(
        make="Intellijel",
        model="Chainsaw",
        year=None,
        variant=None,
        category="synthesizers",
        description=(
            "The Intellijel Chainsaw is an analog waveshaping module with built-in "
            "distortion and overdrive. It features a unique design that allows for "
            "complex harmonic manipulation."
        ),
        features=[
            "Analog waveshaping",
            "Built-in distortion",
            "6HP Eurorack module",
            "CV controllable",
            "Through-zero FM",
        ],
        condition="Very Good",
        condition_notes="Module appears clean with no visible damage.",
        price_low=180.0,
        price_high=250.0,
        confidence="medium",
        notes="The panel branding is unclear. This appears to be a boutique module.",
    )


@pytest.fixture
def claude_confirmed():
    """Claude correctly identified a module — ModularGrid confirms."""
    return SynthIdentification(
        make="Make Noise",
        model="DPO",
        year=None,
        variant=None,
        category="synthesizers",
        description="The Make Noise DPO is a complex oscillator.",
        features=["Analog oscillator", "FM synthesis", "28HP"],
        condition="Excellent",
        condition_notes="",
        price_low=400.0,
        price_high=500.0,
        confidence="high",
        notes="",
    )


@pytest.fixture
def modulargrid_chainsaw():
    """ModularGrid data for the Chainsaw module."""
    return {
        "manufacturer": "Acid Rain Technology",
        "model": "Chainsaw",
        "full_title": "Acid Rain Technology Chainsaw",
        "hp": 4,
        "discontinued": True,
        "subtitle": "Digital Super-Oscillator",
        "description": (
            "Chainsaw is a powerful digital oscillator designed to bring polyphony "
            "to your rack in a compact and playable interface. We were inspired by "
            "the super-saw; a mainstay of electronic music production, and a waveform "
            "rich with harmonics for filters and effects to chew on. Chainsaw features "
            "3 voices of 7 waves, with individual pitch control per voice. All waves "
            "morph from super saw to super square for giant hollow basslines and more."
        ),
        "features": [
            "3 voices of 7 Detuned supersaw-to-supersquare waves",
            "Stereo output (sounds great in mono as well)",
            "3x 1v/o inputs",
            "Coarse tuning in semitones, with encoder push for fine tune",
            "Remembers previous tuning on power cycle, long encoder press to return to C1 (32.7Hz)",
            "Compact, but playable 4hp width",
        ],
        "image_url": "https://modulargrid.net/img/modcache/22227.f.jpg",
        "url": "https://modulargrid.net/e/acid-rain-technology-chainsaw",
    }


@pytest.fixture
def modulargrid_dpo():
    """ModularGrid data for Make Noise DPO (confirmed, not corrected)."""
    return {
        "manufacturer": "Make Noise",
        "model": "DPO",
        "full_title": "Make Noise DPO",
        "hp": 28,
        "discontinued": False,
        "subtitle": "Dual Prismatic Oscillator",
        "description": "The DPO is a complex analog oscillator by Make Noise.",
        "features": ["Analog VCOs", "Through-zero FM", "Waveshaping"],
        "image_url": "https://modulargrid.net/img/modcache/12345.f.jpg",
        "url": "https://modulargrid.net/e/make-noise-dpo",
    }


class TestVerifyWithModularGrid:
    """Test _verify_with_modulargrid replaces Claude's data with ModularGrid's."""

    @patch("synthshop.cli.commands.identify.search_modulargrid")
    def test_corrects_manufacturer(self, mock_search, claude_chainsaw_wrong, modulargrid_chainsaw):
        mock_search.return_value = modulargrid_chainsaw

        result = _verify_with_modulargrid(claude_chainsaw_wrong)

        assert result.make == "Acid Rain Technology"
        assert result.model == "Chainsaw"

    @patch("synthshop.cli.commands.identify.search_modulargrid")
    def test_replaces_description(self, mock_search, claude_chainsaw_wrong, modulargrid_chainsaw):
        mock_search.return_value = modulargrid_chainsaw

        result = _verify_with_modulargrid(claude_chainsaw_wrong)

        # Should use ModularGrid description, not Claude's
        assert "powerful digital oscillator" in result.description
        assert "polyphony" in result.description
        # Claude's wrong description should be gone
        assert "analog waveshaping" not in result.description.lower()
        assert "distortion" not in result.description.lower()

    @patch("synthshop.cli.commands.identify.search_modulargrid")
    def test_replaces_features(self, mock_search, claude_chainsaw_wrong, modulargrid_chainsaw):
        mock_search.return_value = modulargrid_chainsaw

        result = _verify_with_modulargrid(claude_chainsaw_wrong)

        # Claude's wrong features should be gone
        assert "Analog waveshaping" not in result.features
        assert "Built-in distortion" not in result.features
        assert "Through-zero FM" not in result.features

        # ModularGrid features should be present
        assert "Digital Super-Oscillator" in result.features  # subtitle
        assert "4HP Eurorack module" in result.features  # HP
        assert "3 voices of 7 Detuned supersaw-to-supersquare waves" in result.features
        assert "Stereo output (sounds great in mono as well)" in result.features
        assert "3x 1v/o inputs" in result.features
        assert "Discontinued — increasingly rare" in result.features

    @patch("synthshop.cli.commands.identify.search_modulargrid")
    def test_feature_count_matches(self, mock_search, claude_chainsaw_wrong, modulargrid_chainsaw):
        mock_search.return_value = modulargrid_chainsaw

        result = _verify_with_modulargrid(claude_chainsaw_wrong)

        # subtitle + HP + 6 features + discontinued = 9
        assert len(result.features) == 9

    @patch("synthshop.cli.commands.identify.search_modulargrid")
    def test_adds_discontinued_to_notes(self, mock_search, claude_chainsaw_wrong, modulargrid_chainsaw):
        mock_search.return_value = modulargrid_chainsaw

        result = _verify_with_modulargrid(claude_chainsaw_wrong)

        assert result.notes.startswith("Discontinued.")

    @patch("synthshop.cli.commands.identify.search_modulargrid")
    def test_preserves_condition_fields(self, mock_search, claude_chainsaw_wrong, modulargrid_chainsaw):
        """Condition info comes from Claude's photo analysis and should be preserved."""
        mock_search.return_value = modulargrid_chainsaw

        result = _verify_with_modulargrid(claude_chainsaw_wrong)

        assert result.condition == "Very Good"
        assert result.condition_notes == "Module appears clean with no visible damage."

    @patch("synthshop.cli.commands.identify.search_modulargrid")
    def test_preserves_price_range(self, mock_search, claude_chainsaw_wrong, modulargrid_chainsaw):
        """Price range from Claude is preserved (Reverb pricing replaces it later)."""
        mock_search.return_value = modulargrid_chainsaw

        result = _verify_with_modulargrid(claude_chainsaw_wrong)

        assert result.price_low == 180.0
        assert result.price_high == 250.0

    @patch("synthshop.cli.commands.identify.search_modulargrid")
    def test_confirmed_module_also_replaces_description(
        self, mock_search, claude_confirmed, modulargrid_dpo,
    ):
        """Even when manufacturer is correct, description/features come from ModularGrid."""
        mock_search.return_value = modulargrid_dpo

        result = _verify_with_modulargrid(claude_confirmed)

        assert result.make == "Make Noise"
        assert result.model == "DPO"
        # Description should come from ModularGrid
        assert result.description == "The DPO is a complex analog oscillator by Make Noise."

    @patch("synthshop.cli.commands.identify.search_modulargrid")
    def test_confirmed_module_replaces_features(
        self, mock_search, claude_confirmed, modulargrid_dpo,
    ):
        mock_search.return_value = modulargrid_dpo

        result = _verify_with_modulargrid(claude_confirmed)

        # ModularGrid features used instead of Claude's
        assert "Dual Prismatic Oscillator" in result.features  # subtitle
        assert "28HP Eurorack module" in result.features
        assert "Analog VCOs" in result.features
        # Claude's original features should be gone
        assert "FM synthesis" not in result.features

    @patch("synthshop.cli.commands.identify.search_modulargrid")
    def test_fixes_manufacturer_in_notes(self, mock_search, modulargrid_chainsaw):
        """When Claude mentions wrong manufacturer in notes, it gets replaced."""
        wrong = SynthIdentification(
            make="Intellijel",
            model="Chainsaw",
            category="synthesizers",
            description="Wrong.",
            features=["Wrong"],
            condition="Good",
            price_low=100.0,
            price_high=200.0,
            confidence="medium",
            notes="The Chainsaw was discontinued by Intellijel, making it sought-after.",
        )
        mock_search.return_value = modulargrid_chainsaw

        result = _verify_with_modulargrid(wrong)

        assert "Intellijel" not in result.notes
        assert "Acid Rain Technology" in result.notes

    @patch("synthshop.cli.commands.identify.search_modulargrid")
    def test_no_match_preserves_claude_data(self, mock_search, claude_chainsaw_wrong):
        """When ModularGrid has no match, keep Claude's data unchanged."""
        mock_search.return_value = None

        result = _verify_with_modulargrid(claude_chainsaw_wrong)

        assert result.make == "Intellijel"  # Not corrected
        assert "analog waveshaping" in result.description.lower()
        assert "Analog waveshaping" in result.features

    @patch("synthshop.cli.commands.identify.search_modulargrid")
    def test_no_feature_list_shows_metadata_only(self, mock_search):
        """When ModularGrid has no feature list, show subtitle/HP only."""
        claude = SynthIdentification(
            make="Magamart",
            model="LANIAKEA",
            category="synthesizers",
            description="Wrong description.",
            features=["Wavetable oscillator", "Built-in reverb", "Stereo output"],
            condition="Excellent",
            price_low=180.0,
            price_high=250.0,
            confidence="medium",
        )
        mg_data = {
            "manufacturer": "Magerit",
            "model": "LANIAKEA",
            "full_title": "Magerit LANIAKEA",
            "hp": 14,
            "discontinued": False,
            "subtitle": 'A "cosmic" oscillator',
            "description": "The LANIAKEA concept comes from the idea of exploring all possible sound textures.",
            "features": [],  # No feature list on ModularGrid
            "image_url": "https://modulargrid.net/img/modcache/99999.f.jpg",
            "url": "https://modulargrid.net/e/magerit-laniakea",
        }
        mock_search.return_value = mg_data

        result = _verify_with_modulargrid(claude)

        assert result.features == ['A "cosmic" oscillator', "14HP Eurorack module"]

    @patch("synthshop.cli.commands.identify.search_modulargrid")
    def test_no_description_falls_back_to_name_replacement(self, mock_search, claude_chainsaw_wrong):
        """When ModularGrid has no description, fix manufacturer name in Claude's."""
        mg_data = {
            "manufacturer": "Acid Rain Technology",
            "model": "Chainsaw",
            "full_title": "Acid Rain Technology Chainsaw",
            "hp": 4,
            "discontinued": False,
            "subtitle": "Digital Super-Oscillator",
            "description": None,
            "features": [],
            "image_url": None,
            "url": "https://modulargrid.net/e/acid-rain-technology-chainsaw",
        }
        mock_search.return_value = mg_data

        result = _verify_with_modulargrid(claude_chainsaw_wrong)

        # Make should be corrected
        assert result.make == "Acid Rain Technology"
        # Description should have manufacturer name fixed
        assert "Acid Rain Technology Chainsaw" in result.description
        assert "Intellijel" not in result.description

    @patch("synthshop.cli.commands.identify.search_modulargrid")
    def test_no_discontinued_not_added_to_notes(self, mock_search, claude_confirmed, modulargrid_dpo):
        """Non-discontinued modules don't get 'Discontinued' in notes."""
        mock_search.return_value = modulargrid_dpo

        result = _verify_with_modulargrid(claude_confirmed)

        assert "Discontinued" not in result.notes
        assert "Discontinued" not in result.features

    @patch("synthshop.cli.commands.identify._display_module_image")
    @patch("synthshop.cli.commands.identify.search_modulargrid")
    def test_displays_module_image_when_available(
        self, mock_search, mock_display_image, claude_chainsaw_wrong, modulargrid_chainsaw,
    ):
        """When ModularGrid returns an image_url, display it in the terminal."""
        mock_search.return_value = modulargrid_chainsaw

        _verify_with_modulargrid(claude_chainsaw_wrong)

        mock_display_image.assert_called_once_with(modulargrid_chainsaw["image_url"])

    @patch("synthshop.cli.commands.identify._display_module_image")
    @patch("synthshop.cli.commands.identify.search_modulargrid")
    def test_skips_image_when_not_available(self, mock_search, mock_display_image):
        """When ModularGrid has no image_url, don't attempt to display."""
        claude = SynthIdentification(
            make="SomeMaker",
            model="Widget",
            category="synthesizers",
            description="A widget.",
            features=["Feature"],
            condition="Good",
            price_low=100.0,
            price_high=200.0,
            confidence="medium",
        )
        mg_data = {
            "manufacturer": "SomeMaker",
            "model": "Widget",
            "full_title": "SomeMaker Widget",
            "hp": 8,
            "discontinued": False,
            "subtitle": "Filter",
            "description": "A filter module.",
            "features": [],
            "image_url": None,
            "url": "https://modulargrid.net/e/somemaker-widget",
        }
        mock_search.return_value = mg_data

        _verify_with_modulargrid(claude)

        mock_display_image.assert_not_called()

    @patch("synthshop.cli.commands.identify.search_modulargrid")
    def test_swapped_make_model_found(self, mock_search, modulargrid_chainsaw):
        """When Claude swaps make/model (e.g. make='Chainsaw', model='Bo'), still finds match."""
        swapped = SynthIdentification(
            make="Chainsaw",
            model="Bo",
            category="synthesizers",
            description="Wrong description.",
            features=["Wrong feature"],
            condition="Good",
            price_low=100.0,
            price_high=200.0,
            confidence="low",
        )
        # First call with model="Bo" returns None, second call with make="Chainsaw" finds it
        mock_search.return_value = modulargrid_chainsaw

        result = _verify_with_modulargrid(swapped)

        assert result.make == "Acid Rain Technology"
        assert result.model == "Chainsaw"
        assert "powerful digital oscillator" in result.description


class TestDisplayModuleImage:
    """Test _display_module_image downloads and renders images."""

    @patch("synthshop.cli.commands.identify._kitty_display")
    @patch("synthshop.cli.commands.identify.httpx.get")
    def test_downloads_and_displays_image(self, mock_get, mock_kitty):
        from synthshop.cli.commands.identify import _display_module_image

        # Create a tiny 2x2 red PNG in memory
        from io import BytesIO
        from PIL import Image

        img = Image.new("RGB", (2, 2), color=(255, 0, 0))
        buf = BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        mock_response = MagicMock()
        mock_response.content = png_bytes
        mock_get.return_value = mock_response

        _display_module_image("https://modulargrid.net/img/modcache/22227.f.jpg")

        mock_get.assert_called_once()
        mock_kitty.assert_called_once()
        # The image passed to _kitty_display should be a PIL Image
        displayed_img = mock_kitty.call_args[0][0]
        assert isinstance(displayed_img, Image.Image)

    @patch("synthshop.cli.commands.identify._kitty_display")
    @patch("synthshop.cli.commands.identify.httpx.get")
    def test_scales_down_tall_images(self, mock_get, mock_kitty):
        from synthshop.cli.commands.identify import _display_module_image
        from io import BytesIO
        from PIL import Image

        # Create a tall image (100x800)
        img = Image.new("RGB", (100, 800), color=(0, 0, 255))
        buf = BytesIO()
        img.save(buf, format="PNG")

        mock_response = MagicMock()
        mock_response.content = buf.getvalue()
        mock_get.return_value = mock_response

        _display_module_image("https://example.com/tall.jpg", max_height=400)

        mock_kitty.assert_called_once()
        displayed_img = mock_kitty.call_args[0][0]
        assert displayed_img.height == 400

    @patch("synthshop.cli.commands.identify._kitty_display")
    @patch("synthshop.cli.commands.identify.httpx.get")
    def test_silently_skips_on_download_error(self, mock_get, mock_kitty):
        from synthshop.cli.commands.identify import _display_module_image

        mock_get.side_effect = httpx.ConnectError("Network error")

        # Should not raise
        _display_module_image("https://example.com/broken.jpg")

        mock_kitty.assert_not_called()


class TestKittyDisplay:
    """Test _kitty_display sends correct escape sequences."""

    def test_sends_kitty_escape_sequence(self):
        from io import BytesIO, StringIO
        from unittest.mock import patch as _patch

        from PIL import Image

        from synthshop.cli.commands.identify import _kitty_display

        img = Image.new("RGB", (2, 2), color=(255, 0, 0))

        captured = StringIO()
        with _patch("sys.stdout", captured):
            _kitty_display(img)

        output = captured.getvalue()
        # Should start with Kitty graphics escape
        assert "\033_G" in output
        # Should contain the action and format params
        assert "a=T" in output
        assert "f=100" in output
        # Should end with ST (string terminator)
        assert "\033\\" in output
