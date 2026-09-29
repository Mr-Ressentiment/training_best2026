"""Disposable replay spike. No product runtime, network requests, or approvals."""

import argparse
from collections import defaultdict
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
import time


ROOT = Path(__file__).resolve().parent
THRESHOLD = 0.90
MARGIN = 0.15
ORIGIN = "curated_in_session_ai_output_not_independent_api_capture"


def object_schema(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


STRING = {"type": "string"}
FIELD = object_schema({"value": {"type": ["string", "null"]}, "quote": STRING})
CANDIDATE = object_schema({
    "sku": STRING, "score": {"type": "number", "minimum": 0, "maximum": 1},
    "reason": STRING,
})
SCHEMA = object_schema({
    **{key: FIELD for key in ("customer", "po_number", "currency", "declared_total")},
    "extraction_issues": {"type": "array", "items": STRING},
    "lines": {"type": "array", "minItems": 1, "items": object_schema({
        **{key: FIELD for key in ("description", "quantity", "sale_unit", "unit_price")},
        "candidates": {"type": "array", "items": CANDIDATE},
        "ambiguous": {"type": "boolean"}, "uncertainty": STRING,
    })},
})


def reject_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=reject_constant)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def bindings(source, catalog, prompt):
    return {"source_sha256": digest(source), "catalog_sha256": digest(canonical(catalog)),
            "prompt_sha256": digest(prompt), "schema_sha256": digest(canonical(SCHEMA))}


def validate_shape(value, schema, path="response"):
    """Validate only the JSON Schema vocabulary used by SCHEMA; no dependencies."""
    types = schema["type"]
    if isinstance(types, str):
        types = [types]
    checks = {"object": type(value) is dict, "array": type(value) is list,
              "string": type(value) is str, "null": value is None,
              "boolean": type(value) is bool,
              "number": type(value) is int or (type(value) is float and math.isfinite(value))}
    if not any(checks[t] for t in types):
        raise ValueError(f"{path}: expected {types}")
    if type(value) is dict:
        if set(value) != set(schema["required"]):
            raise ValueError(f"{path}: missing or extra keys")
        for key, item in value.items():
            validate_shape(item, schema["properties"][key], f"{path}.{key}")
    elif type(value) is list:
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{path}: empty lines")
        for i, item in enumerate(value):
            validate_shape(item, schema["items"], f"{path}[{i}]")
    elif type(value) in (int, float):
        if not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf):
            raise ValueError(f"{path}: number outside range")


def grounded(field, source, name):
    value, quote = field["value"], field["quote"]
    if value is None:
        if quote:
            raise ValueError(f"{name}: null field must have empty quote")
        return None
    if not value.strip() or value != value.strip() or not quote or quote not in source:
        raise ValueError(f"{name}: missing or nonliteral source evidence")
    if not re.search(r"(?<![\w.])" + re.escape(value) + r"(?![\w.])", quote):
        raise ValueError(f"{name}: value is not supported by quote")
    return value


def quantity(value):
    if value is None:
        return None
    if not re.fullmatch(r"[1-9][0-9]{0,5}", value):
        raise ValueError("quantity: expected integer sale units in 1..999999")
    return int(value)


def money(value):
    if value is None:
        return None
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,2})?", value):
        raise ValueError("money: expected nonnegative decimal with at most two places")
    return Decimal(value)


def amount(value):
    return None if value is None else format(value, ".2f")


def choose_match(line, products):
    candidates = sorted(line["candidates"], key=lambda c: c["score"], reverse=True)
    skus = [c["sku"] for c in candidates]
    if any(sku not in products for sku in skus) or len(set(skus)) != len(skus):
        raise ValueError("candidates: unknown or duplicate SKU")
    score = candidates[0]["score"] if candidates else None
    margin = (round(score - candidates[1]["score"], 6) if len(candidates) > 1
              else score)
    selected = (bool(candidates) and not line["ambiguous"]
                and score >= THRESHOLD and margin >= MARGIN)
    return {"status": "PROPOSED" if selected else "HUMAN_REVIEW",
            "sku": candidates[0]["sku"] if selected else None,
            "confidence": score, "margin": margin,
            "confidence_kind": "uncalibrated_model_score",
            "candidates": candidates, "uncertainty": line["uncertainty"]}


def reconcile(source, response, catalog):
    validate_shape(response, SCHEMA)
    issues = []

    def issue(code, detail, line=None):
        issues.append({"code": code, "line": line, "detail": detail})

    header = {key: grounded(response[key], source, key)
              for key in ("customer", "po_number", "currency", "declared_total")}
    for key, value in header.items():
        if value is None:
            issue("MISSING_FIELD", key)
    declared = money(header["declared_total"])
    customer = header["customer"]
    allowed = catalog["contracts"].get(customer)
    if allowed is None:
        issue("UNKNOWN_CUSTOMER", "No synthetic customer contract; price checks unavailable")
    currency_ok = header["currency"] == catalog["currency"]
    if not currency_ok:
        issue("UNSUPPORTED_CURRENCY", "Only EUR is supported in this experiment")
    for note in response["extraction_issues"]:
        issue("EXTRACTION_UNCERTAINTY", note)
    products = {p["sku"]: p for p in catalog["products"]}
    lines = []
    aggregate = defaultdict(int)
    for number, raw in enumerate(response["lines"], 1):
        fields = {key: grounded(raw[key], source, f"line {number}.{key}")
                  for key in ("description", "quantity", "sale_unit", "unit_price")}
        for key, value in fields.items():
            if value is None:
                issue("MISSING_FIELD", key, number)
        qty, price = quantity(fields["quantity"]), money(fields["unit_price"])
        match = choose_match(raw, products)
        if fields["description"] is None:
            match["sku"] = None
            match["status"] = "HUMAN_REVIEW"
        sku = match["sku"]
        if sku is None:
            issue("SKU_REVIEW", raw["uncertainty"] or "No confident unique match", number)
        unit_ok = sku is not None and fields["sale_unit"] == products[sku]["sale_unit"]
        if sku is not None and not unit_ok:
            issue("UNIT_REVIEW", "No inferred pack/unit conversions", number)
        if sku is not None and allowed is not None and sku not in allowed:
            issue("CONTRACT_NOT_ALLOWED", f"{customer} cannot order {sku}", number)
        if sku is not None and qty is not None and unit_ok:
            aggregate[sku] += qty
        lines.append({"line": number, **fields, "quantity": qty,
                      "unit_price": amount(price), "match": match,
                      "unit_valid": unit_ok,
                      "quoted_line_total": amount(qty * price) if qty is not None and price is not None else None,
                      "tier_quantity": None, "tier_min_quantity": None,
                      "expected_unit_price": None, "expected_line_total": None,
                      "price_delta": None, "price_status": "NOT_CHECKED"})

    # Any unresolved SKU/unit/quantity may affect aggregated tier eligibility.
    quantities_complete = all(line["match"]["sku"] is not None
                              and line["unit_valid"] and line["quantity"] is not None
                              for line in lines)
    for line in lines:
        sku = line["match"]["sku"]
        if (not quantities_complete or not currency_ok or allowed is None
                or sku not in allowed or line["unit_price"] is None):
            continue
        tiers = products[sku]["tiers"]
        tier = max((t for t in tiers if t["min_quantity"] <= aggregate[sku]),
                   key=lambda t: t["min_quantity"])
        expected = money(tier["unit_price"])
        qty = line["quantity"]
        delta = (money(line["unit_price"]) - expected) * qty
        line.update(tier_quantity=aggregate[sku], tier_min_quantity=tier["min_quantity"],
                    expected_unit_price=amount(expected), expected_line_total=amount(qty * expected),
                    price_delta=amount(delta), price_status="PASS" if delta == 0 else "VIOLATION")
        if delta != 0:
            issue("PRICE_RULE_VIOLATION", f"Expected {amount(expected)} EUR per sale unit", line["line"])

    def complete_total(key):
        if not currency_ok or any(line[key] is None for line in lines):
            return None
        return sum((Decimal(line[key]) for line in lines), Decimal("0"))

    quoted_total = complete_total("quoted_line_total")
    expected_total = complete_total("expected_line_total")
    total_delta = quoted_total - declared if quoted_total is not None and declared is not None else None
    if total_delta is not None and total_delta != 0:
        issue("TOTAL_MISMATCH", "Declared net total differs from quantity x quoted price sum")
    if any(i["code"] in {"PRICE_RULE_VIOLATION", "TOTAL_MISMATCH", "CONTRACT_NOT_ALLOWED"} for i in issues):
        status = "RULE_VIOLATION"
    else:
        status = "HUMAN_REVIEW" if issues else "CLEAN_DRAFT"
    return {"status": status, "customer": customer, "po_number": header["po_number"],
            "currency": header["currency"], "lines": lines, "issues": issues,
            "totals": {"declared": amount(declared), "quoted_computed": amount(quoted_total),
                       "contract_expected": amount(expected_total),
                       "quoted_minus_declared": amount(total_delta)},
            "extraction": response, "human_confirmation_required": True,
            "action": "RECONCILIATION_DRAFT_ONLY"}


def process(source_path, recording_path, catalog, prompt, unavailable=False):
    started = time.perf_counter()
    source = source_path.read_text(encoding="utf-8")
    provenance = {"mode": "REPLAY", "origin": ORIGIN, "live_inference": False,
                  "fixture": source_path.name, **bindings(source, catalog, prompt)}
    try:
        if unavailable or not recording_path.is_file():
            raise FileNotFoundError("No AI output available; no automatic fallback")
        recording = read_json(recording_path)
        if type(recording) is not dict:
            raise ValueError("Recording must be a JSON object")
        if recording.get("origin") != ORIGIN:
            raise ValueError("Unexpected recording origin")
        if recording.get("bindings") != bindings(source, catalog, prompt):
            raise ValueError("Stale recording: source/catalog/prompt/schema hash mismatch")
        result = reconcile(source, recording["response"], catalog)
    except (FileNotFoundError, ValueError, KeyError, TypeError) as exc:
        unavailable_error = isinstance(exc, FileNotFoundError)
        result = {"status": "AI_UNAVAILABLE" if unavailable_error else "INVALID_AI_OUTPUT",
                  "issues": [{"code": "AI_UNAVAILABLE" if unavailable_error else "INVALID_AI_OUTPUT",
                              "line": None, "detail": str(exc)}],
                  "lines": [], "totals": None, "human_confirmation_required": True,
                  "action": "RECONCILIATION_DRAFT_ONLY"}
    return {**result, "provenance": provenance,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="One UTF-8 text PO; defaults to all five fixtures")
    parser.add_argument("--recording", type=Path, help="Recording for --input")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--simulate-unavailable", action="store_true")
    args = parser.parse_args()
    if args.recording and not args.input:
        parser.error("--recording requires --input")
    catalog = read_json(ROOT / "catalog.json")
    prompt = (ROOT / "prompt.txt").read_text(encoding="utf-8")
    sources = [args.input] if args.input else sorted((ROOT / "fixtures").glob("*.txt"))
    results = [process(p, args.recording or ROOT / "recordings" / f"{p.stem}.json",
                       catalog, prompt, args.simulate_unavailable) for p in sources]
    payload = {"spike": "ordershield", "synthetic": True, "mode": "REPLAY",
               "live_ai_reliability": "UNVERIFIED", "results": results}
    rendered = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    else:
        print(rendered, end="")
    return int(any(r["status"] in {"AI_UNAVAILABLE", "INVALID_AI_OUTPUT"} for r in results)) * 2


if __name__ == "__main__":
    raise SystemExit(main())
