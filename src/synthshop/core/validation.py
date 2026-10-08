"""Editable draft errors, distinct from conflicts and provider/publication failures."""

FIELD_LABELS = {
    "make": "Maker", "model": "Exact model", "variant": "Revision / variant / package",
    "testing": "Testing / functional condition", "cosmetics": "Cosmetic observations",
    "faults": "Known faults", "modifications": "Modifications", "included": "Included items",
    "condition": "Owner condition", "category_id": "Reverb category",
    "category_name": "Category name", "title": "Title", "description": "Description",
    "price": "Owner asking price", "price_reason": "Private price reasoning / owner override",
    "international_rates": "Additional region rates",
}


class DraftFieldErrors(ValueError):
    """Only errors the owner can correct in the draft's ordinary fields."""

    def __init__(self, errors: dict[str, str], references: dict | None = None):
        self.errors = errors
        self.references = references
        super().__init__(" ".join(errors.values()))
