"""Revisioned local listing and evidence contracts; money never uses binary floats."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
OWNER_FACT_FIELDS = (
    "make",
    "model",
    "variant",
    "testing",
    "cosmetics",
    "faults",
    "modifications",
    "included",
)


def now() -> str:
    """UTC observation time, never inferred sale time."""
    return datetime.now(UTC).isoformat()


class Record(BaseModel):
    """Reject unintended persisted fields."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Photo(Record):
    """Immutable managed original and metadata-free public derivative."""

    id: str
    digest: str
    original_format: str
    width: int
    height: int


class Candidate(Record):
    """Unconfirmed visual identity; no functional condition or price inference."""

    make: str = ""
    model: str = ""
    variant: str = ""
    confidence: Literal["low", "medium", "high"] = "low"
    observations: str = ""
    questions: list[str] = Field(default_factory=list)
    title: str = ""
    description: str = ""


class Comparable(Record):
    """A source observation, not necessarily a completed transaction."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    provider: str
    url: HttpUrl
    source_id: str = ""
    source_class: Literal[
        "sold_display", "active_ask", "confirmed_transaction", "owner_report", "guide_aggregate"
    ]
    provenance: str
    title: str
    make: str = ""
    model: str = ""
    variant: str = ""
    condition: str = ""
    accessories: str = ""
    amount: Money
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    listing_currency: str = "USD"
    shipping: Money | None = None
    shipping_region: str = ""
    tax_treatment: str = "unknown"
    observed_at: str = Field(default_factory=now)
    published_at: str | None = None
    sale_date: str | None = None
    offer_visibility: str = "Accepted offer / paid amount unknown"
    match_rationale: str = "Owner match review required"
    approved_match: bool = False
    excluded_reason: str = ""
    mirror_of: str = ""


class ShippingRate(Record):
    """Explicit destinations; no silent worldwide tariff."""

    region_code: str = "US_CON"
    amount: Money = Decimal("0.00")


class Draft(Record):
    """Exact editable listing, owner facts, and private research."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    revision: int = 1
    created_at: str = Field(default_factory=now)
    make: str = ""
    model: str = ""
    variant: str = ""
    condition: str = ""
    category_id: str = ""
    category_name: str = ""
    testing: str = ""
    cosmetics: str = ""
    faults: str = ""
    modifications: str = ""
    included: str = ""
    title: str = ""
    description: str = ""
    owner_fields: list[str] = Field(default_factory=list)
    candidate: Candidate | None = None
    price: Money | None = None
    currency: Literal["USD"] = "USD"
    price_reason: str = ""
    offers_enabled: bool = True
    shipping: list[ShippingRate] = Field(default_factory=lambda: [ShippingRate()])
    photos: list[Photo] = Field(default_factory=list)
    evidence: list[Comparable] = Field(default_factory=list)
    evidence_identity: str = ""
    research_note: str = "No research collected."
    legacy_remote_id: str | None = None
    legacy_status: str | None = None
    migration_note: str = ""

    def identity(self) -> str:
        """Bind research to the owner-reviewed identity and condition."""
        return "|".join([self.make, self.model, self.variant, self.condition]).casefold()

    def owner_facts(self) -> dict[str, str]:
        """Minimal fact projection safe to send to the vision provider."""
        return {name: getattr(self, name) for name in OWNER_FACT_FIELDS}


CONTRACT = "processed-photo-review-1"
PROFILE = "listing-full-fixed-1"


def manifest_digest(value: dict) -> str:
    """Canonical digest; no timestamps or paths are implicitly discarded."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class LocalPhotoBinding(Record):
    """Intended correspondence, attested by the seller rather than inferred."""

    id: str
    digest: str
    position: int


class PreparationGrant(Record):
    """One authorized create, or a verified historical create binding."""

    contract: str = CONTRACT
    authorized_at: str | None = None
    historical: bool = False
    api_origin: str
    shop_id: str
    shop_slug: str
    backend_binding: str
    photos: list[LocalPhotoBinding]


class Representation(Record):
    """One exact fetched entity; cover is independent of the gallery."""

    identity_kind: str
    identity: str
    relation: str
    locator_digest: str
    digest: str
    byte_count: int
    media_type: str
    format: str
    width: int
    height: int
    blob_id: str = Field(default_factory=lambda: uuid4().hex)

    def projection(self) -> dict:
        """Only the cache identity is incidental to representation equality."""
        return self.model_dump(exclude={"blob_id"})


class ProcessedSnapshot(Record):
    """Immutable persisted envelope; subsequent observations never rewrite approval."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    contract: str = CONTRACT
    profile: str = PROFILE
    captured_at: str = Field(default_factory=now)
    draft_id: str
    revision: int
    fingerprint: str
    payload: dict
    remote_id: str
    target: PreparationGrant
    observed_state: str
    gallery: list[Representation]
    cover: Representation
    digest: str = ""

    def seal(self) -> None:
        """Bind the entire audit envelope, including local cached blob membership."""
        self.digest = manifest_digest(self.model_dump(exclude={"digest"}))

    def verify(self) -> None:
        """Refuse corrupt manifests before serving or consuming an approval."""
        if self.digest != manifest_digest(self.model_dump(exclude={"digest"})):
            raise ValueError("Processed snapshot integrity check failed; refresh evidence.")

    def projection(self) -> dict:
        """Expected draft-to-live transition and fresh capture times are not drift."""
        return {
            "contract": self.contract,
            "profile": self.profile,
            "remote_id": self.remote_id,
            "api_origin": self.target.api_origin,
            "shop_id": self.target.shop_id,
            "shop_slug": self.target.shop_slug,
            "gallery": [entry.projection() for entry in self.gallery],
            "cover": self.cover.projection(),
        }


class FinalApproval(Record):
    """Consumed seller grant for exactly one immutable snapshot."""

    authorized_at: str = Field(default_factory=now)
    snapshot_id: str
    manifest_digest: str


class Attempt(Record):
    """Exactly one durable remote-create opportunity per draft."""

    draft_id: str
    revision: int
    correlation: str
    fingerprint: str
    state: Literal[
        "prepared",
        "creating",
        "remote",
        "review_ready",
        "publishing",
        "published",
        "published_unverified",
        "historical_unverified",
    ] = "prepared"
    remote_id: str | None = None
    url: str | None = None
    error: str = ""
    staged_keys: list[str] = Field(default_factory=list)
    contract: str = CONTRACT
    approved_payload: dict | None = None
    preparation: PreparationGrant | None = None
    snapshot: ProcessedSnapshot | None = None
    final_approval: FinalApproval | None = None
    publish_intent_at: str | None = None
    live_observed: bool = False
    observed_at: str | None = None
    observed_state: str | None = None
