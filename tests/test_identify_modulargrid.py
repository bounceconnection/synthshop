"""Tests for the identify command's ModularGrid integration.

Verifies that when ModularGrid finds a match, the identify command:
- Corrects the manufacturer and model
- Replaces Claude's description with ModularGrid's
- Replaces Claude's features with ModularGrid's
- Adds discontinued status and HP info
"""

from unittest.mock import MagicMock, patch

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
