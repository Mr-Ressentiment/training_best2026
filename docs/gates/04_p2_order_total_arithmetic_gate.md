# P2 Order-Level Arithmetic Human Gate (HG-P2-01)

## Problem

Canonical project specifications and task definitions define `ArithmeticMismatch` with two distinct triggers:

1. **Line-level arithmetic**:
   ```text
   customer-stated line total != extracted quantity × customer-stated unit price
   ```
2. **Order-level arithmetic**:
   ```text
   customer-stated order total != sum of customer-stated line totals
   ```

*(References: `specs/001-ordershield-po-reconciliation/spec.md` FR-010, `data-model.md` §2.7, `tasks.md` T026).*

### Separation of Discrepancy Categories
Discrepancy categories must remain strictly independent:
- **`PriceMismatch`**: Customer-stated unit price != authoritative contract-tier unit price.
- **Line `ArithmeticMismatch`**: Customer-stated line total != quantity × customer-stated unit price.
- **Order `ArithmeticMismatch`**: Customer-stated order total != sum of customer-stated line totals.

For example, if an order has quantity 10, customer unit price $24.00, customer stated line total $240.00, customer stated order total $240.00, but contract-tier price is $25.00:
- `PriceMismatch` = YES (stated $24.00 vs contract $25.00)
- `ArithmeticMismatch` = NO (10 × $24.00 = $240.00, and stated order total $240.00 equals stated line sum $240.00)

The system MUST NOT evaluate order-level arithmetic against contract-priced totals (`calculated_line_total_cents` = quantity × contract_price_cents = $250.00), as doing so would incorrectly conflate pricing with customer arithmetic.

At the time this gate was opened, the extraction, domain, and database schemas provided **no source-backed stated order total field**:

- The canonical data model lacked storage for an extracted/stated order subtotal or grand total.
- The AI extraction payload schema did not parse or validate an order-level total.
- The provenance schema included no location or snippet grounding for an order-level total.

As a result, the deterministic discrepancy evaluation engine (T028) could not evaluate order-level arithmetic discrepancies without an explicit architectural/semantic decision.

T028 implementation could not proceed without either:

1. extending the canonical extraction/domain contracts to represent a source-backed order total (Option A); or
2. formally narrowing the canonical MVP requirement to line-level arithmetic only (Option B).

---

## Pre-Decision Verified State

An audit of the codebase at gate time confirmed the original contract boundaries:

1. **Derived-Only Draft Subtotal**:
   In `app/models/entities.py`, `OrderDraft` originally contained only:
   ```python
   calculated_subtotal_cents = Column(Integer, nullable=False, default=0)
   ```
   This field was derived state populated strictly by deterministic summation of lines (`calculate_subtotal_cents`). No equivalent of `extracted_subtotal_cents` or `stated_order_total_cents` existed on `OrderDraft`.

2. **Extraction Payload Schema Boundary**:
   In `app/models/schemas.py`, `AIExtractionPayload` originally defined only:
   - `customer_name`
   - `customer_id`
   - `po_number`
   - `header_provenance`
   - `line_items` (containing line-level `extracted_quantity`, `extracted_unit_price`, `extracted_line_total`)

   There was no order-level money field in the extraction boundary.

3. **Field Provenance Grounding Schema Boundary**:
   In `app/models/schemas.py`, `FieldProvenanceSchema.field_name` was strictly restricted to:
   ```python
   Literal[
       "customer_name", "po_number", "customer_description",
       "extracted_quantity", "extracted_unit_price", "extracted_line_total",
   ]
   ```
   No citation span or location could be recorded for an order-level total under original Pydantic models and ORM constraints.

4. **Acceptance Suite Sentinel**:
   The original pre-decision sentinel (`test_hg_p2_01_current_model_has_no_source_backed_order_total`) has been superseded by the genuine acceptance tests AC6A–AC6D in `tests/unit/test_reconciliation.py`.

---

## Decision Options

### Option A — Preserve Canonical Requirement (Accepted)

Preserve order-level `ArithmeticMismatch` in the MVP.

Contract work prior to T028 introduces a source-backed optional stated order total through the schema, extraction, domain, and provenance boundaries, followed by an explicit T026 RED acceptance test and T028 implementation.

**Invariants under Option A**:
- AI may extract the document's stated total verbatim from raw text;
- AI MUST NOT calculate or verify whether the stated total is mathematically correct;
- Deterministic reconciliation engine compares the source-backed stated order total against the sum of source-backed stated line totals. This arithmetic check is independent from contract pricing. `calculated_line_total_cents` (quantity × contract_price_cents) MUST NOT be used as the expected value for order-level `ArithmeticMismatch`, because doing so would conflate `PriceMismatch` with `ArithmeticMismatch`;
- Provenance must ground the extracted stated total against canonical `PurchaseOrderDocument.raw_text`;
- Absence of a stated total in customer documents must be handled cleanly (optional field; no false discrepancies if the customer document omitted a grand total);
- If the document contains a stated order total but one or more applicable source-stated line totals are unavailable, the system MUST NOT invent missing line totals and MUST NOT raise an order-level `ArithmeticMismatch` from incomplete arithmetic evidence. (Incomplete source arithmetic remains non-clean / requires the applicable review semantics without inventing an `ArithmeticMismatch` flag).

### Option B — Narrow MVP Arithmetic Scope (Rejected)

Formally remove order-level total reconciliation from the MVP.

Canonical artifacts (`spec.md`, `tasks.md`, `data-model.md`, `api-contracts.md`) would be formally updated so that `ArithmeticMismatch` covers line-level customer arithmetic only:
```text
stated line total != quantity × stated unit price
```

**Implications under Option B**:
- No schema, extraction, or provenance model changes needed;
- Discrepancy engine in T028 evaluates line-level arithmetic only;
- Requires explicit approval and updating of canonical specifications rather than silent deviation.

---

## Recommendation & Decision

**Option A** was recommended and accepted because:

1. **Natural Deterministic Gate**: Comparing customer-stated PO grand total against the sum of customer-stated line totals is a classic commercial discrepancy in wholesale operations and order entry.
2. **Demo Value**: Strengthens the OrderShield demo by catching documents where customer-stated line totals do not sum to the customer's stated PO total.
3. **Bounded Complexity**: The change is localized to schema/payload extension, text grounding, and deterministic integer comparison without requiring architectural redesign.

Project lead accepted Option A on 2026-10-01.

---

## Post-Decision Reconciliation Status

Under contract VLD-P2-HG01R, the canonical boundary reconciliation was completed:

- `AIExtractionPayload.extracted_order_total`: optional source-stated Money (`Money | None = None`)
- `HeaderProvenanceSchema.extracted_order_total`: paired optional provenance (`FieldProvenanceSchema | None = None`)
- `OrderDraft.extracted_order_total_cents`: nullable persisted integer cents (`Integer, nullable=True`)
- `FieldProvenance.field_name = "extracted_order_total"`: added to canonical provenance field names
- `order_service`: intake verifies invariant (`extracted_order_total` iff order-total provenance exists), grounds provenance, and persists exact integer cents
- T026 AC6A–AC6D: genuine RED acceptance tests in `tests/unit/test_reconciliation.py` providing acceptance contract for T028
- Production P2 discrepancy evaluation remains unimplemented pending T028.

---

# Human Gate HG-P2-01

Choose exactly one:

- [x] A — Preserve order-level ArithmeticMismatch and extend source-backed order-total contracts.
- [ ] B — Narrow MVP ArithmeticMismatch to line-level arithmetic only and reconcile canonical specs.

Status: ACCEPTED — OPTION A
Decision date: 2026-10-01
