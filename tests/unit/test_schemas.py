"""T008: validate untrusted extraction structure, authority, and exact money."""

import json
from copy import deepcopy
from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError

from app.models.schemas import (
    AIExtractionPayload,
    AILineItemPayload,
    ErrorResponse,
    FieldProvenanceSchema,
    LocationDataSchema,
    SourceGroundingMismatchError,
    TerminalDraftConflictError,
    cents_to_decimal,
    decimal_to_cents,
)


def provenance(field, snippet="source evidence"):
    return {
        "field_name": field,
        "verbatim_snippet": snippet,
        "location": {"type": "txt", "line_number": 2, "char_offset": 10},
    }


@pytest.fixture
def line():
    return {
        "line_number": 1,
        "customer_description": "18in stretch film heavy duty",
        "extracted_quantity": 10,
        "extracted_unit_price": "25.00",
        "extracted_line_total": "250.00",
        "matched_sku": "SKU-WRAP-18",
        "sku_confidence": "High",
        "sku_resolution_source": "AI_HIGH_CONFIDENCE",
        "candidate_skus": [],
        "matching_rationale": "Exact semantic match",
        "field_provenance": {
            field: provenance(field)
            for field in (
                "customer_description", "extracted_quantity",
                "extracted_unit_price", "extracted_line_total",
            )
        },
    }


@pytest.fixture
def payload(line):
    return {
        "customer_name": "Acme Industrial Supplies",
        "customer_id": "CUST-ACME",
        "po_number": "PO-10023",
        "header_provenance": {
            field: provenance(field) for field in ("customer_name", "po_number")
        },
        "line_items": [line],
    }


def ambiguous(line):
    result = deepcopy(line)
    result.update(
        sku_confidence="Ambiguous", matched_sku=None, sku_resolution_source="NONE",
        candidate_skus=[{
            "sku": "SKU-WRAP-15", "name": "Standard pallet wrap",
            "score": 0.88, "rationale": "Similar product description",
        }],
    )
    return result


@pytest.mark.parametrize("amount,cents", [
    ("25.00", 2500), ("24.50", 2450), ("0.01", 1), ("0.00", 0),
    ("25", 2500), ("25.000", 2500),
    ("123456789012345678901234567890.01", 12345678901234567890123456789001),
])
def test_exact_money_roundtrip(amount, cents, line):
    line["extracted_unit_price"] = amount
    money = AILineItemPayload.model_validate(line).extracted_unit_price
    assert isinstance(money, Decimal)
    assert money == Decimal(amount)
    assert money.as_tuple().exponent == -2
    assert decimal_to_cents(money) == cents
    assert cents_to_decimal(cents) == money
    assert cents_to_decimal(cents).as_tuple().exponent == -2


def test_money_is_independent_of_decimal_context():
    with localcontext() as context:
        context.prec = 2
        assert decimal_to_cents(Decimal("123456789.01")) == 12345678901
        assert cents_to_decimal(12345678901) == Decimal("123456789.01")


@pytest.mark.parametrize("value", [
    "25.001", "-0.01", "NaN", "sNaN", "Infinity", "-Infinity", "nonsense",
    Decimal("25.001"), Decimal("NaN"), Decimal("Infinity"),
    Decimal("-Infinity"), 25.0, True, None,
])
@pytest.mark.parametrize("field", ["extracted_unit_price", "extracted_line_total"])
def test_invalid_money_is_rejected(value, field, line):
    line[field] = value
    with pytest.raises(ValidationError):
        AILineItemPayload.model_validate(line)


@pytest.mark.parametrize("value", [
    Decimal("25.001"), Decimal("-0.01"), Decimal("NaN"),
    Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity"),
    25.0, True, "25.00", 25,
])
def test_decimal_conversion_rejects_invalid_input(value):
    with pytest.raises(ValueError):
        decimal_to_cents(value)


@pytest.mark.parametrize("value", [-1, True, 2500.0, "2500", None])
def test_cents_conversion_requires_nonnegative_integer(value):
    with pytest.raises(ValueError):
        cents_to_decimal(value)


def test_json_money_is_two_place_string(payload):
    model = AIExtractionPayload.model_validate_json(json.dumps(payload))
    result = json.loads(model.model_dump_json())
    assert result["line_items"][0]["extracted_unit_price"] == "25.00"
    assert result["line_items"][0]["extracted_line_total"] == "250.00"
    assert AIExtractionPayload.model_validate(result) == model
    assert isinstance(model.model_dump()["line_items"][0]["extracted_unit_price"], Decimal)


@pytest.mark.parametrize("location", [
    {"type": "txt", "line_number": 2, "char_offset": 10},
    {"type": "pdf", "page_number": 1, "char_start": 50, "char_end": 60},
])
def test_valid_location(location):
    assert LocationDataSchema.model_validate(location).model_dump() == location
    assert LocationDataSchema.model_validate_json(json.dumps(location)).model_dump() == location


@pytest.mark.parametrize("location", [
    {"type": "txt", "page_number": 2},
    {"type": "txt", "line_number": 0, "char_offset": 0},
    {"type": "txt", "line_number": 1, "char_offset": -1},
    {"type": "txt", "line_number": "2", "char_offset": 0},
    {"type": "txt", "line_number": True, "char_offset": 0},
    {"type": "txt", "line_number": 1, "char_offset": 0, "page_number": 1},
    {"type": "pdf", "page_number": 0, "char_start": 50, "char_end": 60},
    {"type": "pdf", "page_number": 1, "char_start": -1, "char_end": 60},
    {"type": "pdf", "page_number": 1, "char_start": 50, "char_end": 50},
    {"type": "pdf", "page_number": 1, "char_start": 50, "char_end": 49},
    {"type": "pdf", "page_number": 1, "char_start": 50},
    {"type": "pdf", "page_number": 1, "char_start": 50, "char_end": 60, "char_offset": 0},
    {"type": "image", "line_number": 1, "char_offset": 0}, {},
])
def test_invalid_location(location):
    with pytest.raises(ValidationError):
        LocationDataSchema.model_validate(location)


@pytest.mark.parametrize("field", [
    "customer_name", "po_number", "customer_description", "extracted_quantity",
    "extracted_unit_price", "extracted_line_total",
])
def test_canonical_provenance_field(field):
    assert FieldProvenanceSchema.model_validate(provenance(field)).field_name == field


@pytest.mark.parametrize("change", [
    {"field_name": "contract_price"}, {"verbatim_snippet": ""},
    {"verbatim_snippet": "   "}, {"location": None}, {"extra": "unknown"},
])
def test_invalid_provenance(change):
    value = provenance("po_number") | change
    with pytest.raises(ValidationError):
        FieldProvenanceSchema.model_validate(value)


@pytest.mark.parametrize("group,field", [
    ("header_provenance", "customer_name"), ("header_provenance", "po_number"),
    ("field_provenance", "customer_description"), ("field_provenance", "extracted_quantity"),
    ("field_provenance", "extracted_unit_price"), ("field_provenance", "extracted_line_total"),
])
@pytest.mark.parametrize("failure", ["missing", "wrong_name"])
def test_all_provenance_is_required_and_key_consistent(group, field, failure, payload):
    values = payload[group] if group == "header_provenance" else payload["line_items"][0][group]
    if failure == "missing":
        del values[field]
    else:
        values[field]["field_name"] = "po_number" if field != "po_number" else "customer_name"
    with pytest.raises(ValidationError):
        AIExtractionPayload.model_validate(payload)


def test_clean_high_confidence_line(line):
    model = AILineItemPayload.model_validate(line)
    assert model.matched_sku == "SKU-WRAP-18"
    assert model.sku_resolution_source == "AI_HIGH_CONFIDENCE"


@pytest.mark.parametrize("field", [
    "line_number", "customer_description", "extracted_quantity",
    "extracted_unit_price", "extracted_line_total", "matched_sku",
    "sku_confidence", "sku_resolution_source", "candidate_skus",
    "matching_rationale", "field_provenance",
])
def test_required_line_fields_are_not_defaulted(field, line):
    del line[field]
    with pytest.raises(ValidationError):
        AILineItemPayload.model_validate(line)


def test_schema_does_not_reconcile_or_verify_raw_source(line):
    line["extracted_line_total"] = "0.01"
    line["field_provenance"]["extracted_unit_price"]["verbatim_snippet"] = "  $25.00  "
    model = AILineItemPayload.model_validate(line)
    assert model.extracted_line_total == Decimal("0.01")
    assert model.field_provenance.extracted_unit_price.verbatim_snippet == "  $25.00  "


def test_ambiguous_line(line):
    model = AILineItemPayload.model_validate(ambiguous(line))
    assert model.matched_sku is None
    assert model.sku_resolution_source == "NONE"
    assert model.candidate_skus[0].score == 0.88


def test_unrecognized_line(line):
    line.update(sku_confidence="Unrecognized", matched_sku=None, sku_resolution_source="NONE")
    assert AILineItemPayload.model_validate(line).matched_sku is None


@pytest.mark.parametrize("confidence,sku,source,candidates", [
    ("High", None, "AI_HIGH_CONFIDENCE", False),
    ("High", "SKU-WRAP-18", "NONE", False),
    ("High", "SKU-WRAP-18", "OPERATOR_SELECTED", False),
    ("Ambiguous", "SKU-WRAP-18", "NONE", True),
    ("Ambiguous", None, "AI_HIGH_CONFIDENCE", True),
    ("Ambiguous", None, "NONE", False),
    ("Unrecognized", "SKU-WRAP-18", "NONE", False),
    ("Unrecognized", None, "AI_HIGH_CONFIDENCE", False),
    ("Unrecognized", None, "OPERATOR_SELECTED", False),
    ("Low", None, "NONE", False),
])
def test_impossible_ai_states(confidence, sku, source, candidates, line):
    value = ambiguous(line) if candidates else line
    value.update(sku_confidence=confidence, matched_sku=sku, sku_resolution_source=source)
    with pytest.raises(ValidationError):
        AILineItemPayload.model_validate(value)


@pytest.mark.parametrize("field,value", [
    ("line_number", 0), ("line_number", True), ("line_number", "1"),
    ("extracted_quantity", 0), ("extracted_quantity", 1.5), ("extracted_quantity", True),
    ("extracted_quantity", "10"), ("customer_description", ""), ("matched_sku", " "),
])
def test_invalid_line_values(field, value, line):
    line[field] = value
    with pytest.raises(ValidationError):
        AILineItemPayload.model_validate(line)


@pytest.mark.parametrize("score", [-0.01, 1.01, float("nan"), float("inf"), True, "0.88"])
def test_invalid_candidate_score(score, line):
    value = ambiguous(line)
    value["candidate_skus"][0]["score"] = score
    with pytest.raises(ValidationError):
        AILineItemPayload.model_validate(value)


@pytest.mark.parametrize("level", ["payload", "line", "candidate", "header", "line_provenance"])
def test_extra_fields_rejected_at_each_ai_level(level, payload):
    targets = {
        "payload": payload, "line": payload["line_items"][0],
        "header": payload["header_provenance"],
        "line_provenance": payload["line_items"][0]["field_provenance"],
    }
    if level == "candidate":
        payload["line_items"][0] = ambiguous(payload["line_items"][0])
        target = payload["line_items"][0]["candidate_skus"][0]
    else:
        target = targets[level]
    target["contract_price"] = "25.00"
    with pytest.raises(ValidationError):
        AIExtractionPayload.model_validate(payload)


@pytest.mark.parametrize("field", ["customer_name", "po_number", "header_provenance", "line_items"])
def test_required_top_level_fields(field, payload):
    del payload[field]
    with pytest.raises(ValidationError):
        AIExtractionPayload.model_validate(payload)


@pytest.mark.parametrize("field,value", [
    ("customer_name", " "), ("po_number", ""), ("line_items", []), ("customer_id", ""),
])
def test_invalid_top_level_values(field, value, payload):
    payload[field] = value
    with pytest.raises(ValidationError):
        AIExtractionPayload.model_validate(payload)


def test_customer_id_can_be_supplied_missing_or_unresolved(payload):
    assert AIExtractionPayload.model_validate(payload).customer_id == "CUST-ACME"
    del payload["customer_id"]
    assert AIExtractionPayload.model_validate(payload).customer_id is None
    payload["customer_id"] = None
    assert AIExtractionPayload.model_validate(payload).customer_id is None


@pytest.mark.parametrize("model", [SourceGroundingMismatchError, TerminalDraftConflictError])
def test_typed_errors(model):
    result = model(message="Human-readable diagnostic")
    assert result.model_dump() == {"error": model.__name__, "message": "Human-readable diagnostic"}
    with pytest.raises(ValidationError):
        model(error="SomeOtherError", message="diagnostic")


@pytest.mark.parametrize("values", [
    {}, {"error": "SomeError"}, {"error": "", "message": "diagnostic"},
    {"error": "SomeError", "message": " "},
])
def test_common_error_requires_explicit_nonempty_fields(values):
    with pytest.raises(ValidationError):
        ErrorResponse.model_validate(values)


def test_common_error():
    assert ErrorResponse(error="SomeError", message="diagnostic").model_dump() == {
        "error": "SomeError", "message": "diagnostic",
    }


def test_json_schema_exposes_locations_and_string_money():
    schema = AIExtractionPayload.model_json_schema(mode="serialization")
    money = schema["$defs"]["AILineItemPayload"]["properties"]["extracted_unit_price"]
    assert money["type"] == "string"
    location = schema["$defs"]["LocationDataSchema"]
    assert location["discriminator"]["propertyName"] == "type"
    assert len(location["oneOf"]) == 2
