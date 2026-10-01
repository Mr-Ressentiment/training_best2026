# P2 Order-Level Arithmetic Human Gate (HG-P2-01)

## Problem

Canonical project specifications and task definitions define `ArithmeticMismatch` with two distinct triggers:

1. **Line-level arithmetic**:
   ```text
   stated line total != extracted quantity × stated unit price
   ```
2. **Order-level arithmetic**:
   ```text
   stated order total != sum of stated line totals
   ```

*(References: `specs/001-ordershield-po-reconciliation/spec.md` FR-010, `data-model.md` §2.7, `tasks.md` T026).*

However, the current extraction, domain, and database schemas provide **no source-backed stated order total field**:

- The canonical data model lacks any storage for an extracted/stated order subtotal or grand total.
- The AI extraction payload schema does not parse or validate an order-level total.
- The provenance schema includes no location or snippet grounding for an order-level total.

As a result, the deterministic discrepancy evaluation engine (T028) cannot evaluate order-level arithmetic discrepancies without an explicit architectural/semantic decision.

T028 implementation cannot proceed without either:

1. extending the canonical extraction/domain contracts to represent a source-backed order total (Option A); or
2. formally narrowing the canonical MVP requirement to line-level arithmetic only (Option B).

---

## Verified Current State

An audit of the codebase confirms the current contract boundaries:

1. **Derived-Only Draft Subtotal**:
   In `app/models/entities.py`, `OrderDraft` contains:
   ```python
   calculated_subtotal_cents = Column(Integer, nullable=False, default=0)
   ```
   This field is derived state populated strictly by deterministic summation of lines (`calculate_subtotal_cents`). No equivalent of `extracted_subtotal_cents` or `stated_order_total_cents` exists on `OrderDraft`.

2. **Extraction Payload Schema Boundary**:
   In `app/models/schemas.py`, `AIExtractionPayload` defines only:
   - `customer_name`
   - `customer_id`
   - `po_number`
   - `header_provenance`
   - `line_items` (containing line-level `extracted_quantity`, `extracted_unit_price`, `extracted_line_total`)

   There is no order-level money field in the extraction boundary.

3. **Field Provenance Grounding Schema Boundary**:
   In `app/models/schemas.py`, `FieldProvenanceSchema.field_name` is strictly restricted to:
   ```python
   Literal[
       "customer_name", "po_number", "customer_description",
       "extracted_quantity", "extracted_unit_price", "extracted_line_total",
   ]
   ```
   No citation span or location can be recorded for an order-level total under current Pydantic models and ORM constraints.

4. **T026 Acceptance Suite Sentinel**:
   Unit test `test_hg_p2_01_current_model_has_no_source_backed_order_total` in `tests/unit/test_reconciliation.py` serves as a sentinel proving that no source-backed order total exists in the current entity model.

---

## Decision Options

### Option A — Preserve Canonical Requirement

Preserve order-level `ArithmeticMismatch` in the MVP.

Follow-up contract work prior to T028 will introduce a source-backed optional stated order total through the schema, extraction, domain, and provenance boundaries, followed by an explicit T026 RED acceptance test and T028 implementation.

**Invariants under Option A**:
- AI may extract the document's stated total verbatim from raw text;
- AI MUST NOT calculate or verify whether the stated total is mathematically correct;
- Deterministic reconciliation engine compares stated total against the calculated line sum;
- Provenance must ground the extracted stated total against canonical `PurchaseOrderDocument.raw_text`;
- Absence of a stated total in customer documents must be handled cleanly (optional field; no false discrepancies if the customer document omitted a grand total).

### Option B — Narrow MVP Arithmetic Scope

Formally remove order-level total reconciliation from the MVP.

Canonical artifacts (`spec.md`, `tasks.md`, `data-model.md`, `api-contracts.md`) will be formally updated so that `ArithmeticMismatch` covers line-level customer arithmetic only:
```text
stated line total != quantity × stated unit price
```

**Implications under Option B**:
- No schema, extraction, or provenance model changes needed;
- Discrepancy engine in T028 evaluates line-level arithmetic only;
- Requires explicit approval and updating of canonical specifications rather than silent deviation.

---

## Recommendation

**Option A** is the recommended direction because:

1. **Natural Deterministic Gate**: Comparing customer-stated PO grand total against line item sum is a classic commercial discrepancy in wholesale operations and order entry.
2. **Demo Value**: Strengthens the OrderShield demo by catching documents where customer line items do not sum to the customer's own PO total.
3. **Bounded Complexity**: The change is localized to schema/payload extension, text grounding, and deterministic integer comparison without requiring architectural redesign.

This recommendation is submitted for project lead decision. Neither option is marked accepted below.

---

# Human Gate HG-P2-01

Choose exactly one:

- [ ] A — Preserve order-level ArithmeticMismatch and extend source-backed order-total contracts.
- [ ] B — Narrow MVP ArithmeticMismatch to line-level arithmetic only and reconcile canonical specs.

Status: PENDING HUMAN DECISION
