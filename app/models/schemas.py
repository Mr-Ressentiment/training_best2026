"""Canonical T008 extraction and provenance contracts, independent of providers.

AI supplies document fields and semantic SKU suggestions only. Contract pricing,
grounding against raw_text, catalog verification, and operator decisions belong
to downstream deterministic services.
"""

from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    RootModel,
    model_validator,
)


def decimal_to_cents(value: Decimal) -> int:
    """Convert finite nonnegative Decimal money to cents without rounding.

    Integer-ratio arithmetic stays exact regardless of the caller's Decimal
    context precision. Floats and sub-cent values are rejected.
    """
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("Money must be a finite nonnegative Decimal")
    numerator, denominator = value.as_integer_ratio()
    cents, remainder = divmod(numerator * 100, denominator)
    if remainder:
        raise ValueError("Money must represent an exact number of cents")
    return cents


def cents_to_decimal(value: int) -> Decimal:
    """Convert nonnegative integer cents to a Decimal with exactly two places."""
    if type(value) is not int or value < 0:
        raise ValueError("Cents must be a nonnegative integer")
    digits = tuple(int(digit) for digit in str(value))
    return Decimal((0, digits, -2))


def _validate_money(value: object) -> Decimal:
    if not isinstance(value, (str, Decimal)) and type(value) is not int:
        raise ValueError("Money requires a decimal string, Decimal, or integer; floats are forbidden")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal money") from exc
    return cents_to_decimal(decimal_to_cents(amount))


Money = Annotated[
    Decimal,
    BeforeValidator(_validate_money),
    PlainSerializer(lambda value: format(value, ".2f"), return_type=str, when_used="json"),
]
# Check nonblank text without stripping or changing verbatim source evidence.
NonEmptyText = Annotated[str, Field(strict=True, min_length=1, pattern=r"\S")]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


class _StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _TXTLocation(_StrictSchema):
    type: Literal["txt"]
    line_number: PositiveInt
    char_offset: NonNegativeInt


class _PDFLocation(_StrictSchema):
    type: Literal["pdf"]
    page_number: PositiveInt
    char_start: NonNegativeInt
    char_end: NonNegativeInt

    @model_validator(mode="after")
    def _validate_span(self) -> "_PDFLocation":
        if self.char_end <= self.char_start:
            raise ValueError("PDF char_end must be greater than char_start")
        return self


class LocationDataSchema(RootModel[
    Annotated[_TXTLocation | _PDFLocation, Field(discriminator="type")]
]):
    """Exclusive TXT/PDF location; dumps directly to the canonical offset object.

    The typed location is accessible through .root; no wrapper appears in JSON.
    """


class FieldProvenanceSchema(_StrictSchema):
    field_name: Literal[
        "customer_name", "po_number", "customer_description",
        "extracted_quantity", "extracted_unit_price", "extracted_line_total",
    ]
    verbatim_snippet: NonEmptyText
    location: LocationDataSchema


# Narrow field_name at each mandatory key to prevent evidence substitution.
class _CustomerNameProvenance(FieldProvenanceSchema):
    field_name: Literal["customer_name"]


class _PONumberProvenance(FieldProvenanceSchema):
    field_name: Literal["po_number"]


class _DescriptionProvenance(FieldProvenanceSchema):
    field_name: Literal["customer_description"]


class _QuantityProvenance(FieldProvenanceSchema):
    field_name: Literal["extracted_quantity"]


class _UnitPriceProvenance(FieldProvenanceSchema):
    field_name: Literal["extracted_unit_price"]


class _LineTotalProvenance(FieldProvenanceSchema):
    field_name: Literal["extracted_line_total"]


class HeaderProvenanceSchema(_StrictSchema):
    customer_name: _CustomerNameProvenance | None
    po_number: _PONumberProvenance | None


class LineItemProvenanceSchema(_StrictSchema):
    customer_description: _DescriptionProvenance
    extracted_quantity: _QuantityProvenance | None
    extracted_unit_price: _UnitPriceProvenance | None
    extracted_line_total: _LineTotalProvenance | None


class CandidateSKUSchema(_StrictSchema):
    """Semantic candidate only; authoritative prices cannot enter this boundary."""

    sku: NonEmptyText
    name: NonEmptyText
    score: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    rationale: NonEmptyText


class AILineItemPayload(_StrictSchema):
    line_number: PositiveInt
    customer_description: NonEmptyText
    # Explicit null means not extracted; keys remain required, without defaults.
    extracted_quantity: PositiveInt | None
    extracted_unit_price: Money | None
    extracted_line_total: Money | None
    matched_sku: NonEmptyText | None
    sku_confidence: Literal["High", "Ambiguous", "Unrecognized"]
    sku_resolution_source: Literal["AI_HIGH_CONFIDENCE", "NONE"]
    candidate_skus: list[CandidateSKUSchema]
    matching_rationale: NonEmptyText
    field_provenance: LineItemProvenanceSchema

    @model_validator(mode="after")
    def _validate_field_provenance(self) -> "AILineItemPayload":
        for field in (
            "customer_description", "extracted_quantity",
            "extracted_unit_price", "extracted_line_total",
        ):
            if (getattr(self, field) is None) != (getattr(self.field_provenance, field) is None):
                raise ValueError(f"{field} value and provenance must both be null or both present")
        return self

    @model_validator(mode="after")
    def _validate_sku_state(self) -> "AILineItemPayload":
        if self.sku_confidence == "High":
            if self.matched_sku is None or self.sku_resolution_source != "AI_HIGH_CONFIDENCE":
                raise ValueError("High confidence requires a matched SKU and AI_HIGH_CONFIDENCE")
        else:
            if self.matched_sku is not None or self.sku_resolution_source != "NONE":
                raise ValueError("Ambiguous and Unrecognized lines must remain unresolved")
            if self.sku_confidence == "Ambiguous" and not self.candidate_skus:
                raise ValueError("Ambiguous lines require candidate SKU choices")
        return self


class AIExtractionPayload(_StrictSchema):
    """Accept incomplete extraction; readiness is decided downstream."""

    customer_name: NonEmptyText | None
    # An unverified extraction hint, not a verified customer/contract lookup.
    # Resolved customer identity remains a downstream readiness prerequisite.
    customer_id: NonEmptyText | None = None
    po_number: NonEmptyText | None
    header_provenance: HeaderProvenanceSchema
    line_items: Annotated[list[AILineItemPayload], Field(min_length=1)]

    @model_validator(mode="after")
    def _validate_header_provenance(self) -> "AIExtractionPayload":
        for field in ("customer_name", "po_number"):
            if (getattr(self, field) is None) != (getattr(self.header_provenance, field) is None):
                raise ValueError(f"{field} value and provenance must both be null or both present")
        return self


class ErrorResponse(_StrictSchema):
    error: NonEmptyText
    message: NonEmptyText


class SourceGroundingMismatchError(ErrorResponse):
    error: Literal["SourceGroundingMismatchError"] = "SourceGroundingMismatchError"


class TerminalDraftConflictError(ErrorResponse):
    error: Literal["TerminalDraftConflictError"] = "TerminalDraftConflictError"
