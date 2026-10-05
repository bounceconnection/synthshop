"""Photo/fact/research orchestration behind the local web UI, without HTML presentation."""

import json
from decimal import Decimal

from pydantic import TypeAdapter, ValidationError

from synthshop.core.config import Settings
from synthshop.core.models import OWNER_FACT_FIELDS, Comparable, Draft, Money, ShippingRate
from synthshop.core.photos import MAX_PHOTOS, PhotoLibrary
from synthshop.core.pricing import from_reverb, recommendation
from synthshop.core.product_store import DraftConflictError, DraftStore
from synthshop.integrations.openai_vision import identify_from_photos
from synthshop.integrations.reverb import ReverbClient

EDITABLE = OWNER_FACT_FIELDS + (
    "condition",
    "category_id",
    "category_name",
    "title",
    "description",
)
MONEY = TypeAdapter(Money)


def owner_text(value) -> str:
    """Bounded, trimmed owner-entered text."""
    value = str(value).strip()
    if len(value) > 12000:
        raise ValueError("Each text field must be at most 12,000 characters.")
    return value


def money(value: str, field: str) -> Decimal:
    """Owner-entered USD amounts fail with a field-specific message, never a server error."""
    try:
        return MONEY.validate_python(value.strip())
    except ValidationError as exc:
        raise ValueError(f"{field} must be a USD amount such as 50.00.") from exc


class Application:
    """Each successful mutation makes a revision and invalidates previous review."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = DraftStore(settings.data_dir)
        self.photos = PhotoLibrary(self.store.root)
        self.import_errors = self.store.import_legacy(settings.products_dir)

    def current(self, draft_id: str, revision: int) -> Draft:
        """Reject stale actions before expensive or remote read operations."""
        draft = self.store.load(draft_id)
        if draft.revision != revision:
            raise DraftConflictError("This tab is stale. Reload the current revision.")
        return draft

    def upload(
        self, files: list[bytes], draft_id: str | None = None, revision: int | None = None
    ) -> Draft:
        """Local intake only; no Reverb or image staging side effects."""
        draft = self.current(draft_id, revision) if draft_id else Draft()
        if not files or len(files) + len(draft.photos) > MAX_PHOTOS:
            raise ValueError("Upload 1–25 photos total per draft.")
        photos = [self.photos.add(content) for content in files]
        draft.photos.extend(photos)
        return self.save(draft, revision)

    def save(self, draft: Draft, revision: int | None) -> Draft:
        """Price and reasoning the owner has not changed follow the current recommendation."""
        pricing = recommendation(draft)
        if "price" not in draft.owner_fields:
            draft.price = pricing["ask"]
        if "price_reason" not in draft.owner_fields:
            draft.price_reason = pricing["rationale"] if pricing["ask"] else ""
        return self.store.save(draft, revision)

    def edit(self, draft_id: str, revision: int, fields: dict) -> Draft:
        """Owner corrections win, including explicit nonfunctioning/poor condition."""
        draft = self.current(draft_id, revision)
        for name in EDITABLE:
            if name in fields:
                value = owner_text(fields[name])
                changed = value != getattr(draft, name)
                if changed:
                    setattr(draft, name, value)
                if (changed or value) and name not in draft.owner_fields:
                    draft.owner_fields.append(name)
        self.own_pricing(draft, fields)
        draft.offers_enabled = fields.get("offers_enabled") in (True, "on", "true")
        rates = [ShippingRate()]
        for line in str(fields.get("international_rates", "")).splitlines():
            if line.strip():
                code, separator, amount = line.partition("=")
                code = code.strip()
                if not separator:
                    raise ValueError("Enter each shipping rate as CODE=amount, such as CA=50.00.")
                if code == "US_CON":
                    raise ValueError("Continental-US shipping must remain explicitly free.")
                rates.append(
                    ShippingRate(region_code=code, amount=money(amount, f"Shipping rate {code}"))
                )
        if len({rate.region_code for rate in rates}) != len(rates):
            raise ValueError("Duplicate shipping destinations")
        draft.shipping = rates
        if not draft.title:
            draft.title = " ".join(filter(None, [draft.make, draft.model, draft.variant]))
        if not draft.description:
            draft.description = self.factual_copy(draft)
        return self.save(draft, revision)

    @staticmethod
    def own_pricing(draft: Draft, fields: dict) -> None:
        """A changed price/reason becomes the owner's; blank returns it to the recommendation."""
        pricing = {}
        if "price" in fields:
            price = owner_text(fields["price"] or "")
            pricing["price"] = money(price, "Asking price") if price else None
        if "price_reason" in fields:
            pricing["price_reason"] = owner_text(fields["price_reason"])
        for name, value in pricing.items():
            if value is None or value == "":
                if name in draft.owner_fields:
                    draft.owner_fields.remove(name)
            elif value != getattr(draft, name) and name not in draft.owner_fields:
                draft.owner_fields.append(name)
            setattr(draft, name, value)

    def reorder(self, draft_id: str, revision: int, ids: list[str]) -> Draft:
        """First image is cover. Require an exact permutation, not arbitrary photo references."""
        draft = self.current(draft_id, revision)
        if len(ids) != len(draft.photos) or set(ids) != {photo.id for photo in draft.photos}:
            raise ValueError("Photo order must contain each current photo exactly once.")
        by_id = {photo.id: photo for photo in draft.photos}
        draft.photos = [by_id[photo_id] for photo_id in ids]
        return self.save(draft, revision)

    def analyze(self, draft_id: str, revision: int) -> Draft:
        """Model proposes; owner facts/copy never disappear during regeneration."""
        draft = self.current(draft_id, revision)
        self.photos.verify(draft.photos)
        candidate = identify_from_photos(
            [self.photos.path(photo.id) for photo in draft.photos],
            draft,
            self.settings,
        )
        draft.candidate = candidate
        for name in ("make", "model", "variant"):
            if name not in draft.owner_fields:
                setattr(draft, name, getattr(candidate, name))
        if "title" not in draft.owner_fields:
            draft.title = candidate.title or " ".join(
                filter(None, [draft.make, draft.model, draft.variant])
            )
        if "description" not in draft.owner_fields:
            draft.description = candidate.description or self.factual_copy(draft)
        draft.research_note = "Identity proposal updated; review the candidate and pricing match."
        return self.save(draft, revision)

    @staticmethod
    def factual_copy(draft: Draft) -> str:
        """Ground public unit claims in owner facts, not unconstrained generated prose."""
        paragraphs = [text for text in (draft.cosmetics, draft.testing) if text]
        if draft.faults:
            paragraphs.append("Known faults: " + draft.faults)
        if draft.modifications:
            paragraphs.append("Modifications: " + draft.modifications)
        if draft.included.strip():
            paragraphs.append(
                "Included:\n"
                + "\n".join(
                    "-" + line.strip().lstrip("-")
                    for line in draft.included.splitlines()
                    if line.strip()
                )
            )
        return "\n\n".join(paragraphs)

    def research(self, draft_id: str, revision: int) -> Draft:
        """Two bounded anonymous reads; never send private owner facts to marketplace search."""
        draft = self.current(draft_id, revision)
        if not draft.make or not draft.model:
            raise ValueError("Confirm maker and exact model before research.")
        query = " ".join(filter(None, [draft.make, draft.model, draft.variant]))
        collected = []
        rejected = 0
        with ReverbClient(self.settings) as client:
            for sold in (True, False):
                for row in client.search(query, sold=sold):
                    try:
                        collected.append(from_reverb(row, draft, sold=sold))
                    except (ValueError, KeyError, TypeError):
                        rejected += 1
        # Current manual evidence remains, but must match this identity afresh if it changed.
        manual = [
            comp for comp in draft.evidence if not comp.provenance.startswith("Anonymous Reverb")
        ]
        if draft.evidence_identity != draft.identity():
            for comp in manual:
                comp.approved_match = False
        draft.evidence = manual + collected
        draft.evidence_identity = draft.identity()
        draft.research_note = (
            "Two bounded Reverb searches (up to 40 sold + 40 active). Sold-tagged displayed prices "
            "are not paid/accepted-offer amounts. Publication dates are not sale dates. "
            "Filter support/retention is not guaranteed; no restricted Price Guide scraping. "
            f"{rejected} malformed observations omitted. Review exact matches and packages below."
        )
        return self.save(draft, revision)

    def import_evidence(self, draft_id: str, revision: int, text: str) -> Draft:
        """Owner-pasted JSON observations; URLs are citations, never automatically fetched."""
        draft = self.current(draft_id, revision)
        if len(text) > 200_000:
            raise ValueError("Evidence import exceeds 200 KB")
        rows = json.loads(text)
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
            raise ValueError("Import a JSON array of 1–100 observations.")
        observations = []
        for number, row in enumerate(rows, 1):
            try:
                observations.append(Comparable.model_validate(row))
            except ValidationError as exc:
                names = sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]})
                raise ValueError(
                    f"Observation {number}: check {', '.join(names) or 'its fields'}."
                ) from exc
        for comp in observations:
            comp.provenance = "Owner-imported: " + comp.provenance
            comp.approved_match = False
        draft.evidence.extend(observations)
        if not draft.evidence_identity:
            draft.evidence_identity = draft.identity()
        return self.save(draft, revision)

    def review_evidence(
        self, draft_id: str, revision: int, comp_id: str, include: bool, rationale: str
    ) -> Draft:
        """Explicit match review must explain variant/package/condition decisions."""
        draft = self.current(draft_id, revision)
        if draft.evidence_identity != draft.identity():
            raise ValueError("Research is stale. Refresh research for this identity first.")
        comp = next((row for row in draft.evidence if row.id == comp_id), None)
        if comp is None or not rationale.strip():
            raise ValueError("Supply a match/exclusion rationale for the observation.")
        comp.approved_match = include
        comp.match_rationale = rationale.strip()
        comp.excluded_reason = "" if include else rationale.strip()
        return self.save(draft, revision)
