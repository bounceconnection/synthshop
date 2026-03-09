"""ModularGrid lookup for verifying eurorack module identification.

Used as a fallback when Claude Vision identifies a eurorack module — checks
ModularGrid to verify/correct the manufacturer and get additional details.
"""

import asyncio
import re
from collections.abc import Callable

import httpx

_TIMEOUT = 10.0
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def search_modulargrid(
    model_name: str,
    make_hint: str | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict | None:
    """Search for a eurorack module on ModularGrid.

    Strategy:
    1. If make_hint given, try the direct URL: /e/{make}-{model}
       - If it exists, Claude was right — return it
       - If 404, Claude likely got the make wrong
    2. Search DuckDuckGo for the module on ModularGrid
    3. If search fails, brute-force common manufacturer slugs

    Args:
        model_name: The model name to search for.
        make_hint: Claude's guess at the manufacturer.
        on_progress: Optional callback for status updates (e.g. spinner text).

    Returns dict with manufacturer, model, hp, discontinued, url — or None.
    """
    _progress = on_progress or (lambda msg: None)
    model_slug = _slugify(model_name)

    # Step 1: Try Claude's identified manufacturer directly
    if make_hint:
        _progress(f"Trying {make_hint}...")
        make_slug = _slugify(make_hint)
        result = fetch_module_page(f"https://modulargrid.net/e/{make_slug}-{model_slug}")
        if result:
            return result

    # Step 2: Try DuckDuckGo search
    _progress("Searching web...")
    slug = _search_ddg(model_name)
    if slug:
        return fetch_module_page(f"https://modulargrid.net/e/{slug}")

    # Step 3: Try DuckDuckGo with make + model (sometimes finds different results)
    if make_hint:
        slug = _search_ddg(f"{make_hint} {model_name}")
        if slug:
            return fetch_module_page(f"https://modulargrid.net/e/{slug}")

    # Step 4: Brute force — try common eurorack manufacturer slugs
    result = _try_common_manufacturers(model_slug, on_progress=_progress)
    if result:
        return result

    return None


def _slugify(name: str) -> str:
    """Convert a name to a ModularGrid URL slug."""
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


# Common eurorack manufacturers — tried as URL prefixes when search fails.
# Sorted roughly by popularity so we find matches faster.
_COMMON_MANUFACTURERS = [
    "make-noise", "mutable-instruments", "intellijel", "doepfer", "tiptop-audio",
    "4ms", "befaco", "erica-synths", "wmd", "noise-engineering",
    "qu-bit-electronix", "michigan-synth-works", "mannequins", "xaoc-devices",
    "steady-state-fate", "acid-rain-technology", "joranalogue", "instruo",
    "endorphin-es", "happy-nerding", "after-later-audio", "alm-busy-circuits",
    "moffenzeef-modular", "synthesis-technology", "verbos-electronics",
    "pittsburgh-modular", "malekko-heavy-industry", "bastl-instruments",
    "shakmat-modular", "dreadbox", "2hp", "circuit-abbey", "vpme-de",
    "mystic-circuits", "folktek", "rabid-elephant", "addac-system",
    "ritual-electronics", "neuzeit-instruments", "antimatter-audio",
    "mosaic", "blue-lantern", "frequency-central", "st-modular",
    "wmdevices", "abstract-data", "dannysound", "ladik",
]


_BATCH_SIZE = 10  # Number of concurrent HEAD requests


def _try_common_manufacturers(
    model_slug: str,
    on_progress: Callable[[str], None] | None = None,
) -> dict | None:
    """Try fetching ModularGrid pages with common manufacturer prefixes.

    Sends HEAD requests in parallel batches for speed, then fetches
    the full page only for the first match.
    """
    _progress = on_progress or (lambda msg: None)
    base = "https://modulargrid.net/e"
    total = len(_COMMON_MANUFACTURERS)

    for batch_start in range(0, total, _BATCH_SIZE):
        batch = _COMMON_MANUFACTURERS[batch_start:batch_start + _BATCH_SIZE]
        batch_end = min(batch_start + len(batch), total)
        names = ", ".join(s.replace("-", " ").title() for s in batch[:3])
        _progress(f"Checking manufacturers {batch_start + 1}–{batch_end}/{total} ({names}...)")

        hit_url = asyncio.run(_check_batch(base, model_slug, batch))
        if hit_url:
            _progress("Found match, fetching details...")
            return fetch_module_page(hit_url)

    return None


async def _check_batch(base: str, model_slug: str, make_slugs: list[str]) -> str | None:
    """Check a batch of manufacturer slugs concurrently. Returns first matching URL."""
    async with httpx.AsyncClient(
        headers={"User-Agent": _UA}, timeout=5.0, follow_redirects=True,
    ) as client:
        tasks = [
            _head_check(client, f"{base}/{slug}-{model_slug}")
            for slug in make_slugs
        ]
        results = await asyncio.gather(*tasks)

    for url in results:
        if url:
            return url
    return None


async def _head_check(client: httpx.AsyncClient, url: str) -> str | None:
    """HEAD request; returns the URL if it exists (200), else None."""
    try:
        r = await client.head(url)
        if r.status_code == 200:
            return url
    except httpx.HTTPError:
        pass
    return None


def _search_ddg(model_name: str) -> str | None:
    """Search DuckDuckGo for a ModularGrid module page and return its slug."""
    try:
        response = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": f"site:modulargrid.net {model_name} eurorack module"},
            headers={"User-Agent": _UA},
            timeout=_TIMEOUT,
            follow_redirects=True,
        )
        if response.status_code != 200:
            return None
    except httpx.HTTPError:
        return None

    # Extract module slugs from results
    all_slugs = re.findall(r'modulargrid\.net/e/([\w-]+)', response.text)

    skip = (
        "modules", "forum", "offers", "vendors", "racks", "marketplace",
        "about", "users", "search", "patches",
    )
    seen = set()
    module_slugs = []
    for slug in all_slugs:
        if slug not in seen and not slug.startswith(skip):
            seen.add(slug)
            module_slugs.append(slug)

    if not module_slugs:
        return None

    # Prefer slugs containing the model name
    model_lower = model_name.lower().replace(" ", "-")
    for slug in module_slugs:
        if model_lower in slug:
            return slug

    return module_slugs[0]


def _extract_description_and_features(html: str) -> tuple[str | None, list[str]]:
    """Extract the full description and feature list from a ModularGrid module page.

    The description and features live inside `<div id="module-details">` in the
    static HTML. The structure is:
        <p class="lead ...">Subtitle</p>
        <p>Full description paragraph(s)...</p>
        <ul><li>Feature 1</li>...</ul>
    """
    # Find the #module-details content
    detail_match = re.search(
        r'id="module-details"[^>]*>(.*?)(?=<(?:hr|div\s|section\s))',
        html,
        re.DOTALL,
    )
    if not detail_match:
        return None, []

    detail_html = detail_match.group(1)

    # Extract description paragraphs (skip the <p class="lead"> subtitle)
    paragraphs = re.findall(r'<p(?:\s[^>]*)?>(.+?)</p>', detail_html, re.DOTALL)
    desc_parts = []
    for p in paragraphs:
        # Skip the subtitle (class="lead") and link-only paragraphs
        if 'class="lead' in detail_html.split(p)[0].rsplit('<p', 1)[-1]:
            continue
        # Strip HTML tags
        text = re.sub(r'<[^>]+>', '', p).strip()
        # Skip paragraphs that are just URLs
        if text and not text.startswith('http'):
            desc_parts.append(text)

    description = " ".join(desc_parts) if desc_parts else None

    # Extract features from <ul><li> items
    features = []
    li_matches = re.findall(r'<li>(.+?)</li>', detail_html, re.DOTALL)
    for li in li_matches:
        text = re.sub(r'<[^>]+>', '', li).strip()
        if text:
            features.append(text)

    return description, features


def _extract_title(html: str) -> str | None:
    """Extract the module title from og:title or <title> tag."""
    og_match = re.search(r'property="og:title"[^>]*content="([^"]+)"', html, re.IGNORECASE)
    if og_match:
        return og_match.group(1).strip()

    title_match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
    if title_match:
        title = title_match.group(1).strip()
        return re.sub(r'\s*[-–]\s*Eurorack Module.*$', '', title, flags=re.IGNORECASE)

    return None


def _extract_manufacturer_and_model(title: str, url: str) -> tuple[str, str]:
    """Split a ModularGrid title into manufacturer and model using the URL slug."""
    slug_match = re.search(r'/e/([\w-]+)$', url)

    if slug_match:
        slug = slug_match.group(1)
        words = title.split()
        for i in range(len(words) - 1, 0, -1):
            candidate_make = " ".join(words[:i])
            make_slug = candidate_make.lower().replace(" ", "-")
            if slug.startswith(make_slug):
                return candidate_make, " ".join(words[i:])

    words = title.split()
    if len(words) >= 2:
        return " ".join(words[:-1]), words[-1]
    return title, title


def _extract_subtitle(html: str) -> str | None:
    """Extract module subtitle from <p class="lead"> or og:description."""
    lead_match = re.search(
        r'<p\s+class="lead[^"]*">(.*?)</p>', html, re.DOTALL | re.IGNORECASE
    )
    if lead_match:
        subtitle = re.sub(r'<[^>]+>', '', lead_match.group(1)).strip()
        if subtitle:
            return subtitle

    og_desc = re.search(
        r'property="og:description"[^>]*content="([^"]+)"', html, re.IGNORECASE
    )
    if og_desc:
        parts = og_desc.group(1).strip().split(" - ")
        if len(parts) >= 3:
            return parts[-1].strip()

    return None


def fetch_module_page(url: str) -> dict | None:
    """Fetch a ModularGrid module page and extract key details.

    Returns dict with manufacturer, model, full_title, hp, discontinued, url.
    Returns None on 404 or failure.
    """
    try:
        response = httpx.get(
            url, headers={"User-Agent": _UA}, timeout=_TIMEOUT, follow_redirects=True,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    html = response.text

    title = _extract_title(html)
    if not title:
        return None

    manufacturer, model_name = _extract_manufacturer_and_model(title, url)

    hp_match = re.search(r'(\d+)\s*HP', html)
    description, features = _extract_description_and_features(html)
    img_match = re.search(r'href="(/img/modcache/\d+\.f\.jpg)"', html)

    return {
        "manufacturer": manufacturer,
        "model": model_name,
        "full_title": title,
        "hp": int(hp_match.group(1)) if hp_match else None,
        "discontinued": bool(re.search(r'discontinued', html, re.IGNORECASE)),
        "subtitle": _extract_subtitle(html),
        "description": description,
        "features": features,
        "image_url": f"https://modulargrid.net{img_match.group(1)}" if img_match else None,
        "url": url,
    }
