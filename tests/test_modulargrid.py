"""Tests for ModularGrid lookup and data extraction."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from synthshop.integrations.modulargrid import (
    _extract_description_and_features,
    _slugify,
    _try_common_manufacturers,
    fetch_module_page,
    search_modulargrid,
)


# --- Sample HTML fixtures ---

CHAINSAW_MODULE_DETAILS = """
<div id="module-details">
                <p class="lead wrap">Digital Super-Oscillator</p><p>Chainsaw is a powerful digital oscillator designed to bring polyphony to your rack in a compact and playable interface. We were inspired by the super-saw; a mainstay of electronic music production, and a waveform rich with harmonics for filters and effects to chew on. Chainsaw features 3 voices of 7 waves, with individual pitch control per voice. All waves morph from super saw to super square for giant hollow basslines and more.</p>

<ul>
<li>3 voices of 7 Detuned supersaw-to-supersquare waves</li>
<li>Stereo output (sounds great in mono as well)</li>
<li>3x 1v/o inputs</li>
<li>Coarse tuning in semitones, with encoder push for fine tune</li>
<li>Remembers previous tuning on power cycle, long encoder press to return to C1 (32.7Hz)</li>
<li>Compact, but playable 4hp width</li>
</ul>
<p><a href="https://acidraintechnology.com/products/chainsaw" target="blank">https://acidraintechnology.com/products/chainsaw</a></p>            </div>
            <hr>
"""

CHAINSAW_FULL_HTML = """
<html>
<head>
<meta property="og:title" content="Acid Rain Technology Chainsaw">
<meta property="og:description" content="Acid Rain Technology Chainsaw - Eurorack Module - Digital Super-Oscillator">
<title>Acid Rain Technology Chainsaw - Eurorack Module on ModularGrid</title>
</head>
<body>
<div class="box-specs"><dl><dt>Dimensions</dt><dd>4 HP</dd></dl></div>
<p class="text-error">This Module is discontinued.</p>
""" + CHAINSAW_MODULE_DETAILS + """
</body>
</html>
"""

# Module with description but no feature list (like Mutable Instruments Plaits)
DESCRIPTION_ONLY_HTML = """
<html>
<head>
<meta property="og:title" content="Mutable Instruments Plaits">
<meta property="og:description" content="Mutable Instruments Plaits - Eurorack Module - Macro-oscillator">
<title>Mutable Instruments Plaits</title>
</head>
<body>
<div class="box-specs"><dl><dt>Dimensions</dt><dd>12 HP</dd></dl></div>
<p class="text-error">This Module is discontinued.</p>
<div id="module-details">
                <p class="lead wrap">Macro-oscillator</p><p>Plaits is the spiritual successor of Mutable Instruments best-selling voltage-controlled sound source, Braids.</p>

<p>Just like its predecessor, it offers direct access to a large palette of easily tweakable sounds.</p>
            </div>
            <hr>
</body>
</html>
"""

# Module with no description at all
MINIMAL_HTML = """
<html>
<head>
<meta property="og:title" content="SomeMaker Widget">
<meta property="og:description" content="SomeMaker Widget - Eurorack Module - Filter">
<title>SomeMaker Widget</title>
</head>
<body>
<div class="box-specs"><dl><dt>Dimensions</dt><dd>8 HP</dd></dl></div>
</body>
</html>
"""


# --- _slugify tests ---


class TestSlugify:
    def test_simple_name(self):
        assert _slugify("Chainsaw") == "chainsaw"

    def test_multi_word(self):
        assert _slugify("Acid Rain Technology") == "acid-rain-technology"

    def test_special_chars(self):
        assert _slugify("Make Noise DPO") == "make-noise-dpo"

    def test_strips_leading_trailing(self):
        assert _slugify("  Clouds  ") == "clouds"


# --- _extract_description_and_features tests ---


class TestExtractDescriptionAndFeatures:
    def test_extracts_chainsaw_description(self):
        description, features = _extract_description_and_features(CHAINSAW_FULL_HTML)
        assert description is not None
        assert "powerful digital oscillator" in description
        assert "polyphony" in description

    def test_excludes_subtitle_from_description(self):
        description, _ = _extract_description_and_features(CHAINSAW_FULL_HTML)
        # The subtitle "Digital Super-Oscillator" is in a <p class="lead"> and should be skipped
        assert description is not None
        assert not description.startswith("Digital Super-Oscillator")

    def test_excludes_url_paragraphs(self):
        description, _ = _extract_description_and_features(CHAINSAW_FULL_HTML)
        assert description is not None
        assert "acidraintechnology.com" not in description

    def test_extracts_chainsaw_features(self):
        _, features = _extract_description_and_features(CHAINSAW_FULL_HTML)
        assert len(features) == 6
        assert "3 voices of 7 Detuned supersaw-to-supersquare waves" in features
        assert "Stereo output (sounds great in mono as well)" in features
        assert "3x 1v/o inputs" in features
        assert "Compact, but playable 4hp width" in features

    def test_description_only_no_features(self):
        description, features = _extract_description_and_features(DESCRIPTION_ONLY_HTML)
        assert description is not None
        assert "spiritual successor" in description
        assert features == []

    def test_no_module_details_section(self):
        description, features = _extract_description_and_features(MINIMAL_HTML)
        assert description is None
        assert features == []

    def test_multi_paragraph_description_joined(self):
        description, _ = _extract_description_and_features(DESCRIPTION_ONLY_HTML)
        assert description is not None
        # Both paragraphs should be joined with a space
        assert "Braids." in description
        assert "easily tweakable sounds" in description


# --- fetch_module_page tests ---


class TestFetchModulePage:
    @patch("synthshop.integrations.modulargrid.httpx.get")
    def test_extracts_all_fields(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = CHAINSAW_FULL_HTML
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = fetch_module_page("https://modulargrid.net/e/acid-rain-technology-chainsaw")

        assert result is not None
        assert result["manufacturer"] == "Acid Rain Technology"
        assert result["model"] == "Chainsaw"
        assert result["hp"] == 4
        assert result["discontinued"] is True
        assert result["subtitle"] == "Digital Super-Oscillator"

    @patch("synthshop.integrations.modulargrid.httpx.get")
    def test_returns_description(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = CHAINSAW_FULL_HTML
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = fetch_module_page("https://modulargrid.net/e/acid-rain-technology-chainsaw")

        assert result is not None
        assert result["description"] is not None
        assert "powerful digital oscillator" in result["description"]

    @patch("synthshop.integrations.modulargrid.httpx.get")
    def test_returns_features(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = CHAINSAW_FULL_HTML
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = fetch_module_page("https://modulargrid.net/e/acid-rain-technology-chainsaw")

        assert result is not None
        assert len(result["features"]) == 6
        assert "3x 1v/o inputs" in result["features"]

    @patch("synthshop.integrations.modulargrid.httpx.get")
    def test_no_features_when_absent(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = DESCRIPTION_ONLY_HTML
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = fetch_module_page("https://modulargrid.net/e/mutable-instruments-plaits")

        assert result is not None
        assert result["features"] == []
        assert result["description"] is not None

    @patch("synthshop.integrations.modulargrid.httpx.get")
    def test_no_description_when_section_missing(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = MINIMAL_HTML
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = fetch_module_page("https://modulargrid.net/e/somemaker-widget")

        assert result is not None
        assert result["description"] is None
        assert result["features"] == []

    @patch("synthshop.integrations.modulargrid.httpx.get")
    def test_404_returns_none(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = fetch_module_page("https://modulargrid.net/e/nonexistent-module")
        assert result is None

    @patch("synthshop.integrations.modulargrid.httpx.get")
    def test_http_error_returns_none(self, mock_get):
        mock_get.side_effect = httpx.HTTPError("Connection failed")

        result = fetch_module_page("https://modulargrid.net/e/acid-rain-technology-chainsaw")
        assert result is None


# --- search_modulargrid tests ---


class TestSearchModularGrid:
    @patch("synthshop.integrations.modulargrid.fetch_module_page")
    def test_finds_with_direct_url(self, mock_fetch):
        """When make_hint is correct, finds module on first try."""
        mock_fetch.return_value = {
            "manufacturer": "Acid Rain Technology",
            "model": "Chainsaw",
            "full_title": "Acid Rain Technology Chainsaw",
            "hp": 4,
            "discontinued": True,
            "subtitle": "Digital Super-Oscillator",
            "description": "Chainsaw is a powerful digital oscillator.",
            "features": ["3 voices", "Stereo output"],
            "url": "https://modulargrid.net/e/acid-rain-technology-chainsaw",
        }

        result = search_modulargrid("Chainsaw", make_hint="Acid Rain Technology")

        assert result is not None
        assert result["manufacturer"] == "Acid Rain Technology"
        assert result["description"] == "Chainsaw is a powerful digital oscillator."
        # Should have tried direct URL first
        mock_fetch.assert_called_once_with(
            "https://modulargrid.net/e/acid-rain-technology-chainsaw"
        )

    @patch("synthshop.integrations.modulargrid._try_common_manufacturers")
    @patch("synthshop.integrations.modulargrid._search_ddg")
    @patch("synthshop.integrations.modulargrid.fetch_module_page")
    def test_falls_back_to_ddg_when_direct_fails(self, mock_fetch, mock_ddg, mock_brute):
        """When direct URL 404s, tries DuckDuckGo search."""
        mock_fetch.side_effect = [
            None,  # Direct URL fails
            {"manufacturer": "Acid Rain Technology", "model": "Chainsaw",
             "full_title": "Acid Rain Technology Chainsaw", "hp": 4,
             "discontinued": True, "subtitle": "Digital Super-Oscillator",
             "description": "A powerful oscillator.", "features": ["Stereo"],
             "url": "https://modulargrid.net/e/acid-rain-technology-chainsaw"},
        ]
        mock_ddg.return_value = "acid-rain-technology-chainsaw"

        result = search_modulargrid("Chainsaw", make_hint="Intellijel")

        assert result is not None
        assert result["manufacturer"] == "Acid Rain Technology"
        mock_ddg.assert_called_once()

    @patch("synthshop.integrations.modulargrid._try_common_manufacturers")
    @patch("synthshop.integrations.modulargrid._search_ddg")
    @patch("synthshop.integrations.modulargrid.fetch_module_page")
    def test_returns_none_when_nothing_found(self, mock_fetch, mock_ddg, mock_brute):
        mock_fetch.return_value = None
        mock_ddg.return_value = None
        mock_brute.return_value = None

        result = search_modulargrid("NonexistentModule123", make_hint="FakeMaker")
        assert result is None

    @patch("synthshop.integrations.modulargrid.fetch_module_page")
    def test_progress_callback_called(self, mock_fetch):
        mock_fetch.return_value = {
            "manufacturer": "Make Noise", "model": "DPO",
            "full_title": "Make Noise DPO", "hp": 28,
            "discontinued": False, "subtitle": "Dual Prismatic Oscillator",
            "description": "A complex oscillator.", "features": [],
            "url": "https://modulargrid.net/e/make-noise-dpo",
        }
        progress_messages = []

        search_modulargrid(
            "DPO", make_hint="Make Noise",
            on_progress=lambda msg: progress_messages.append(msg),
        )

        assert len(progress_messages) >= 1
        assert any("Make Noise" in msg for msg in progress_messages)


# --- _try_common_manufacturers tests ---


class TestTryCommonManufacturers:
    @patch("synthshop.integrations.modulargrid.fetch_module_page")
    @patch("synthshop.integrations.modulargrid._check_batch")
    def test_returns_match_from_batch(self, mock_check_batch, mock_fetch):
        """When a batch finds a matching URL, fetches and returns the full page."""
        mock_check_batch.side_effect = [
            None,  # First batch: no match
            "https://modulargrid.net/e/acid-rain-technology-chainsaw",  # Second batch: hit
        ]
        mock_fetch.return_value = {
            "manufacturer": "Acid Rain Technology",
            "model": "Chainsaw",
            "full_title": "Acid Rain Technology Chainsaw",
            "hp": 4,
            "discontinued": True,
            "subtitle": "Digital Super-Oscillator",
            "description": "A powerful digital oscillator.",
            "features": ["Stereo output"],
            "url": "https://modulargrid.net/e/acid-rain-technology-chainsaw",
        }

        result = _try_common_manufacturers("chainsaw")

        assert result is not None
        assert result["manufacturer"] == "Acid Rain Technology"
        mock_fetch.assert_called_once_with(
            "https://modulargrid.net/e/acid-rain-technology-chainsaw"
        )

    @patch("synthshop.integrations.modulargrid._check_batch")
    def test_returns_none_when_no_match(self, mock_check_batch):
        """When no batch finds a match, returns None."""
        mock_check_batch.return_value = None

        result = _try_common_manufacturers("nonexistent-module-xyz")
        assert result is None

    @patch("synthshop.integrations.modulargrid._check_batch")
    def test_progress_reports_batch_ranges(self, mock_check_batch):
        """Progress callback shows batch ranges, not individual manufacturers."""
        mock_check_batch.return_value = None
        messages = []

        _try_common_manufacturers(
            "test-module",
            on_progress=lambda msg: messages.append(msg),
        )

        assert len(messages) >= 1
        # Should report batch ranges like "1–10/48"
        assert any("1–10" in msg for msg in messages)
