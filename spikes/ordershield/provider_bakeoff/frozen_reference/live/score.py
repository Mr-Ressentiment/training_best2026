"""Provider-independent scoring. Reference labels are never sent to inference."""
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ordershield import SCHEMA, THRESHOLD, MARGIN, grounded, reconcile, validate_shape

HEADER = ("customer", "po_number", "currency", "declared_total")
LINE = ("description", "quantity", "sale_unit", "unit_price")


def normalized(value):
    return " ".join(value.split()) if isinstance(value, str) else value


def score_response(response, source, truth, catalog):
    result = {"schema_valid": False, "field_correct": 0, "field_expected": 8,
              "field_checks": {}, "all_fields_correct": False,
              "provenance_valid": 0, "provenance_expected": 8,
              "nonnull_evidence_valid": 0,
              "nonnull_evidence_expected": sum(v is not None for v in truth["header"] + truth["line"]),
              "all_provenance_valid": False, "proposed_sku": None, "sku_correct": False,
              "confident_proposal": False, "wrong_confident": False,
              "review_success": False, "ambiguous_clean": False,
              "core_status": None, "core_error": None, "core_result": None}
    try:
        validate_shape(response, SCHEMA)
    except (ValueError, TypeError, OverflowError) as exc:
        result["schema_error"] = str(exc)
        return result
    result["schema_valid"] = True
    fields = [(k, response[k], v) for k, v in zip(HEADER, truth["header"])]
    fields += [(f"line.{k}", response["lines"][0][k], v) for k, v in zip(LINE, truth["line"])]
    for name, field, expected in fields:
        correct = normalized(field["value"]) == normalized(expected)
        result["field_checks"][name] = correct
        result["field_correct"] += int(correct)
        try:
            grounded(field, source, name)
            # Null counts only when ground truth is also absent.
            valid = field["value"] is not None or expected is None
            result["provenance_valid"] += int(valid)
            result["nonnull_evidence_valid"] += int(valid and field["value"] is not None and expected is not None)
        except ValueError:
            pass
    one_line = len(response["lines"]) == 1
    result["all_fields_correct"] = one_line and result["field_correct"] == 8
    result["all_provenance_valid"] = one_line and result["provenance_valid"] == 8
    proposals = []
    for line in response["lines"]:
        # Also count high-confidence invented SKUs, even though the core's
        # catalog-membership guard correctly rejects them afterward.
        candidates = sorted(line["candidates"], key=lambda c: c["score"], reverse=True)
        top = candidates[0]["score"] if candidates else 0
        margin = round(top - candidates[1]["score"], 6) if len(candidates) > 1 else top
        proposed = bool(candidates) and not line["ambiguous"] and top >= THRESHOLD and margin >= MARGIN
        proposals.append(candidates[0]["sku"] if proposed else None)
    result["proposed_sku"] = proposals[0] if one_line else proposals
    result["confident_proposal"] = any(s is not None for s in proposals)
    result["wrong_confident"] = any(s is not None and
                                    (i > 0 or s != truth["sku"]) for i, s in enumerate(proposals))
    result["sku_correct"] = one_line and proposals[0] == truth["sku"]
    try:
        core = reconcile(source, response, catalog)
        result.update(core_status=core["status"], core_result=core)
        result["review_success"] = one_line and proposals[0] is None and core["status"] == "HUMAN_REVIEW"
        result["ambiguous_clean"] = truth["needs_sku_review"] and core["status"] == "CLEAN_DRAFT"
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        result["core_error"] = str(exc)
    return result


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "rate": round(numerator / denominator, 6) if denominator else None}


def distribution(values):
    if not values:
        return None
    values = sorted(values)
    return {"count": len(values), "min": min(values), "median": statistics.median(values),
            "mean": statistics.mean(values), "p90": values[math.ceil(.90 * len(values)) - 1],
            "p95": values[math.ceil(.95 * len(values)) - 1], "max": max(values)}


def summarize(attempts, expected):
    # The runner includes an empty score for unsuccessful attempts, so every
    # attempted failure remains in denominators instead of disappearing.
    scores = [a["score"] for a in attempts]
    ambiguous = [a for a in attempts if a["fixture"] in {"h05_no_size", "h06_no_pack"}]
    reviews = [a for a in attempts if expected[a["fixture"]]["needs_sku_review"]]
    determinate = [a for a in attempts if expected[a["fixture"]]["sku"] is not None]
    dangerous = [a for a in attempts if expected[a["fixture"]]["dangerous"]]
    wrong = sum(s["wrong_confident"] for s in scores)
    categories = {}
    for a in attempts:
        categories[a["outcome"]] = categories.get(a["outcome"], 0) + 1
    return {
        "planned_calls": 30, "attempted_calls": len(attempts), "unattempted_calls": 30 - len(attempts),
        "complete": len(attempts) == 30, "unattempted_metrics": "UNMEASURED",
        "schema_valid_per_attempt": ratio(sum(s["schema_valid"] for s in scores), len(attempts)),
        "schema_valid_per_scheduled": ratio(sum(s["schema_valid"] for s in scores), 30) if len(attempts) == 30 else None,
        "correct_fields": ratio(sum(s["field_correct"] for s in scores), 8 * len(attempts)),
        "all_fields_correct": ratio(sum(s["all_fields_correct"] for s in scores), len(attempts)),
        "correct_resolved_sku": ratio(sum(a["score"]["sku_correct"] for a in determinate), len(determinate)),
        "correct_abstention": ratio(sum(a["score"]["sku_correct"] for a in reviews), len(reviews)),
        "ambiguous_to_review": ratio(sum(a["score"]["review_success"] for a in ambiguous), len(ambiguous)),
        "ambiguous_to_clean": ratio(sum(a["score"]["ambiguous_clean"] for a in ambiguous), len(ambiguous)),
        "all_review_cases_to_review": ratio(sum(a["score"]["review_success"] for a in reviews), len(reviews)),
        "all_review_cases_to_clean": ratio(sum(a["score"]["ambiguous_clean"] for a in reviews), len(reviews)),
        "wrong_confident_per_attempt": ratio(wrong, len(attempts)),
        "wrong_confident_per_confident": ratio(wrong, sum(s["confident_proposal"] for s in scores)),
        "dangerous_wrong_confident": ratio(sum(a["score"]["wrong_confident"] for a in dangerous), len(dangerous)),
        "nonnull_source_evidence": ratio(sum(s["nonnull_evidence_valid"] for s in scores), sum(s["nonnull_evidence_expected"] for s in scores)),
        "provenance_or_valid_missingness": ratio(sum(s["provenance_valid"] for s in scores), 8 * len(attempts)),
        "all_provenance_valid": ratio(sum(s["all_provenance_valid"] for s in scores), len(attempts)),
        "cli_session_provenance": ratio(sum(bool(a["provider"]["session_id"]) for a in attempts), len(attempts)),
        "outcomes": categories,
        "latency_seconds_all": distribution([a["provider"]["elapsed_seconds"] for a in attempts]),
        "latency_seconds_valid_core": distribution([a["provider"]["elapsed_seconds"] for a in attempts if a["outcome"] == "CORE_RESULT"]),
        "per_fixture": {name: {
            "attempts": sum(a["fixture"] == name for a in attempts),
            "schema_valid": sum(a["score"]["schema_valid"] for a in attempts if a["fixture"] == name),
            "all_fields_correct": sum(a["score"]["all_fields_correct"] for a in attempts if a["fixture"] == name),
            "proposed_skus": [a["score"]["proposed_sku"] for a in attempts if a["fixture"] == name],
            "core_statuses": [a["score"]["core_status"] for a in attempts if a["fixture"] == name],
            "wrong_confident": sum(a["score"]["wrong_confident"] for a in attempts if a["fixture"] == name),
            "latencies_seconds": [a["provider"]["elapsed_seconds"] for a in attempts if a["fixture"] == name],
        } for name in expected},
    }
