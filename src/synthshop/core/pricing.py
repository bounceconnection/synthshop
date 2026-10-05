"""Conservative comparable matching and traceable, non-FX price recommendations."""

import re
from decimal import Decimal
from statistics import median

from synthshop.core.models import Comparable, Draft


def normalized(value: str) -> str:
    """Punctuation/case normalization only, never fuzzy variant equivalence."""
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def from_reverb(row: dict, draft: Draft, *, sold: bool) -> Comparable:
    """Preserve source semantics, including currency conversion and unknown sale dates."""
    state = row.get("state", {}).get("slug")
    price = row.get("price", {})
    title = row.get("title", "")
    condition = row.get("condition", {}).get("display_name", "")
    reasons = []
    if state != ("sold" if sold else "live"):
        reasons.append("Returned state does not match requested sold/live class")
    if normalized(row.get("make", "")) != normalized(draft.make):
        reasons.append("Different or unknown maker")
    if normalized(row.get("model", "")) != normalized(draft.model):
        reasons.append("Different or unknown model/variant")
    if draft.variant and normalized(draft.variant) not in normalized(title):
        reasons.append("Required variant not established")
    for term in ("clone", "replica", "bundle", "lot", "parts", "broken", "panel only", "case only"):
        if term in normalized(title) and term not in normalized(draft.model + " " + draft.variant):
            reasons.append(f"Potential mismatched {term}")
    if not draft.condition or normalized(condition) != normalized(draft.condition):
        reasons.append("Condition differs or owner condition not supplied")
    shipping = None
    for rate in row.get("shipping", {}).get("rates", []):
        if rate.get("region_code") == "US_CON" and rate.get("rate", {}).get("currency") == "USD":
            shipping = rate["rate"].get("amount")
    url = row.get("_links", {}).get("self", {}).get("web", {}).get("href")
    if not url:
        url = f"https://reverb.com/item/{row['id']}"
    return Comparable(
        provider="Reverb",
        url=url,
        source_id=str(row["id"]),
        source_class="sold_display" if sold else "active_ask",
        provenance="Anonymous Reverb listing API; observed state=" + str(state),
        title=title,
        make=row.get("make", ""),
        model=row.get("model", ""),
        condition=condition,
        amount=price["amount"],
        currency=price["currency"],
        listing_currency=row.get("listing_currency", "unknown"),
        shipping=shipping,
        shipping_region="US_CON" if shipping is not None else "unknown",
        published_at=row.get("published_at"),
        tax_treatment=(
            "included"
            if row.get("tax_included") is True
            else "excluded"
            if row.get("tax_included") is False
            else "unknown"
        ),
        excluded_reason="; ".join(reasons),
        match_rationale="Structured maker/model comparison; verify edition, package, condition "
        "and duplicate/relist history before including.",
    )


def recommendation(draft: Draft) -> dict:
    """Only reviewed exact matches in one currency/destination/class enter arithmetic."""
    excluded = {}
    grouped: dict[str, list[Comparable]] = {}
    seen = set()
    for comp in draft.evidence:
        key = (comp.provider.casefold(), comp.source_id or str(comp.url).split("?", maxsplit=1)[0])
        reason = comp.excluded_reason
        if draft.evidence_identity != draft.identity():
            reason = "Research is stale after identity/condition correction"
        elif key in seen or comp.mirror_of:
            reason = "Duplicate/mirrored observation"
        elif not comp.approved_match:
            reason = reason or "Owner match review required"
        elif comp.currency != "USD" or comp.listing_currency != "USD":
            reason = "Unsupported/original foreign currency; no sale-date FX assumed"
        elif comp.shipping is None or comp.shipping_region != "US_CON":
            reason = "Unknown/incomparable shipping; not assumed zero"
        elif comp.tax_treatment == "included":
            reason = "Tax-inclusive amount; not normalized to tax-exclusive comparison"
        elif comp.source_class in ("guide_aggregate", "owner_report"):
            reason = "Context only; not independently comparable item-level evidence"
        seen.add(key)
        if reason:
            excluded[comp.id] = reason
        else:
            grouped.setdefault(comp.source_class, []).append(comp)
    chosen = next(
        (
            kind
            for kind in ("confirmed_transaction", "sold_display")
            if len(grouped.get(kind, [])) >= 2
        ),
        None,
    )
    if chosen is None:
        return {
            "ask": None,
            "rationale": "Insufficient comparable sold evidence. Enter an explicit "
            "owner price and reason; active asks and single observations are context only.",
            "excluded": excluded,
            "groups": grouped,
        }
    sample = grouped[chosen]
    values = sorted(comp.amount + comp.shipping for comp in sample)
    low, high = values[0], values[-1]
    if len(values) >= 5:
        low, high = values[len(values) // 4], values[(3 * len(values)) // 4]
    ask = Decimal(median(values)).quantize(Decimal("0.01"))
    return {
        "ask": str(ask),
        "low": str(low),
        "high": str(high),
        "source_class": chosen,
        "rationale": f"USD {low}–{high} item + stated shipping; suggested free-US_CON ask ${ask} "
        f"is the median of {len(sample)} reviewed {chosen} observations. "
        + (
            "Small sample; full observed range, not a confident market estimate. "
            if len(sample) < 5
            else "Central half of reviewed observations. "
        )
        + "Sold displayed prices may conceal offers; sale dates/recency can be unknown. "
        "Additional taxes/fees are not normalized. "
        "No condition/accessory adjustments or currency conversion invented.",
        "sources": [comp.id for comp in sample],
        "excluded": excluded,
        "groups": grouped,
    }
