# Integration Points Map: 002-symbolic-advisory

**Feature Branch**: `002-symbolic-advisory`  
**Base Branch**: `main` (canonical Phase 1–6 OrderShield implementation)  
**Date**: 2026-10-02  
**Status**: Draft / Complete Research  

---

## 1. Overview & Classification Legend

This document maps all architectural integration points between the existing OrderShield codebase and the proposed `002-symbolic-advisory` feature. Every referenced path and symbol is grounded in the current codebase.

### Evidence Classifications
- **Verified Fact**: Explicitly verified by inspecting the canonical implementation on `main`.
- **Proposal**: Proposed design or architectural integration for `002-symbolic-advisory` [PROPOSED/NEW].
- **Open Question**: An unresolved design decision or product ambiguity marked `[NEEDS CLARIFICATION]` requiring a human decision gate.

---

## 2. Master Integration Points Matrix

| Concern | Existing File & Exact Symbol | Current Behavior / Data Contract | Potential 002 Integration | Gap, Risk, or Decision Needed | Evidence Type |
|:---|:---|:---|:---|:---|:---:|
| **Reconciliation Engine** | `app/services/reconciliation.py`<br>`evaluate_clean_draft` | Evaluates active draft lines against catalog and contract tiers. Creates `DiscrepancyFlag(severity="Blocking", resolution_state="Unresolved")`. Sets draft status to `"Needs Review"` (if discrepancies exist or data incomplete) or `"Ready for Approval"`. Fails closed if terminal (`Approved`/`Rejected`). | Advisory engine runs downstream of `evaluate_clean_draft`, consuming the resulting active flags and line states to produce explainable guidance. | **Boundary Risk**: Advisory must not be called inside `evaluate_clean_draft` to keep discrepancy evaluation strictly deterministic and side-effect free. | Verified Fact / Proposal |
| **Line Total Calculation** | `app/services/reconciliation.py`<br>`calculate_line_total_cents` | Exact integer arithmetic: `quantity * contract_price_cents`. Fails closed on boolean/float types or negative values. | Used by advisory to recalculate line totals when advising operators on packaging increments or contract tier changes. | **None**: Helper is pure and fully reusable. | Verified Fact |
| **Subtotal Calculation** | `app/services/reconciliation.py`<br>`calculate_subtotal_cents` | Exact integer arithmetic: `sum(line_totals_cents)`. Fails closed on non-integers, negative numbers, or invalid iterables. | Used by advisory to calculate hypothetical active line sums and compare them against customer stated order totals. | **None**: Pure function, zero side effects. | Verified Fact |
| **Contract Tier Selection** | `app/services/reconciliation.py`<br>`select_contract_price_tier` | Queries `ContractPriceTier` joined on `CustomerContract`. Filters `customer_id`, `sku`, `min_quantity <= quantity`. Fails closed (`PricingConflictError`) if multiple contracts match. Returns highest matching tier or `None`. | Advisory queries this function or the underlying tiers to explain tier thresholds and suggest quantity bump targets. | **Decision**: Advisory needs to inspect *all* tiers for a contract to advise on volume discounts, whereas this function only returns the single active tier for quantity `Q`. | Verified Fact / Proposal |
| **Discrepancy Entity & ORM** | `app/models/entities.py`<br>`DiscrepancyFlag` | ORM table `discrepancy_flags`. Columns: `id`, `draft_id`, `line_item_id` (nullable), `discrepancy_type`, `severity`, `expected_value`, `requested_value`, `explanation`, `resolution_state`. Restricted types: `PriceMismatch`, `QuantityOrPackagingBreach`, `ArithmeticMismatch`, `CatalogMatchingMismatch`. | Primary input to Symbolic Advisory. Advisory evaluates each unresolved flag to produce actionable guidance. | **Schema Invariant**: Advisory must not alter `DiscrepancyFlag` columns or add new unapproved enum values to SQLite constraints. | Verified Fact |
| **Order Draft Entity** | `app/models/entities.py`<br>`OrderDraft` | Columns: `id`, `document_id`, `customer_id`, `customer_name_extracted`, `po_number_extracted`, `status`, `calculated_subtotal_cents`, `extracted_order_total_cents`, `rejection_reason`, `is_replay_mode`, timestamps. Relationships: `discrepancy_flags`, `line_items`, `provenance_records`, `verified_order`, `audit_events`. | Read-only input for advisory. Advisory accesses `extracted_order_total_cents` and active line items to analyze holistic order health. | **Decision**: Should advisory results be stored on `OrderDraft` (e.g. JSON column/relation) or computed dynamically on read? | Verified Fact / Open Question |
| **Draft Line Item Entity** | `app/models/entities.py`<br>`DraftLineItem` | Columns: `id`, `draft_id`, `line_number`, `customer_description`, `extracted_quantity`, `extracted_unit_price_cents`, `extracted_line_total_cents`, `matched_sku`, `sku_confidence`, `sku_resolution_source`, `candidate_skus_json`, `matching_rationale`, `contract_price_cents`, `calculated_line_total_cents`, `status` (`Active`/`Removed`). | Advisory uses line fields, candidate SKUs, and contract price to evaluate line-level advice. | **Invariant**: Advisory must never mutate line columns directly. | Verified Fact |
| **Catalog Master Entity** | `app/models/entities.py`<br>`CatalogProduct` | Columns: `sku` (PK), `name`, `category`, `unit_of_measure`, `base_price_cents`, `min_order_quantity`, `package_increment`. Relationships: `tiers`, `line_items`. | Advisory reads `min_order_quantity` and `package_increment` to compute compliant quantity adjustments for packaging breaches. | **None**: Read-only domain master data. | Verified Fact |
| **Customer Contract & Tiers** | `app/models/entities.py`<br>`CustomerContract`<br>`ContractPriceTier` | `CustomerContract`: `id`, `customer_id`, `customer_name`, `valid_from`, `valid_to`. `ContractPriceTier`: `id`, `contract_id`, `sku`, `min_quantity`, `tier_price_cents`. | Advisory reads tier records to identify potential discount opportunities when quantities are near volume thresholds. | **None**: Read-only contract lookup. | Verified Fact |
| **Order Intake Call Site** | `app/services/order_service.py`<br>`ingest_order` | Parses document, creates `PurchaseOrderDocument`, `OrderDraft`, `DraftLineItem`, `FieldProvenance`, and audit events. Calls `evaluate_clean_draft(db, draft)`. Commits transaction. | Potential hook to trigger advisory calculation or caching during intake. | **Architecture**: If advisory is computed on-the-fly at serialization, intake needs no modification. If persisted, intake must invoke advisory before commit. | Verified Fact / Open Question |
| **Grounded Field Correction** | `app/services/reconciliation.py`<br>`correct_line_field` | Validates value & snippet grounding in `PurchaseOrderDocument.raw_text`. Updates line field, updates `FieldProvenance`, marks matching flags `ResolvedByCorrection`, calls `evaluate_clean_draft`. Caller owns commit. | Advisory provides suggested values, but operator must initiate correction. Grounding check remains an absolute security barrier. | **Safety Invariant**: Advisory recommendations cannot override `SourceGroundingMismatchError`. | Verified Fact |
| **Line Removal** | `app/services/reconciliation.py`<br>`remove_line` | Sets line `status = "Removed"`, marks flags `ResolvedByLineRemoval`, calls `evaluate_clean_draft`. Caller owns commit. | Advisory may recommend line removal if a line item cannot be matched to catalog or resolved. | **Invariant**: Removal remains explicit human operator decision. | Verified Fact |
| **SKU Selection** | `app/services/reconciliation.py`<br>`select_line_sku` | Validates SKU in `CatalogProduct`, updates `matched_sku`, sets `sku_resolution_source = "OPERATOR_SELECTED"`, resolves flags, calls `evaluate_clean_draft`. Caller owns commit. | Advisory surfaces prioritized candidates from `candidate_skus_json` or catalog search to guide operator selection. | **None**: Fully compatible with existing operator workflow. | Verified Fact |
| **Draft Inspection Endpoint** | `app/api/routes_drafts.py`<br>`get_draft` | HTTP GET `/api/v1/drafts/{draft_id}`. Fetches draft from DB and calls `serialize_draft(draft)`. | Endpoint where advisory notices and order-level flags should be exposed to client. | **Integration Decision**: Expose advisories directly in `serialize_draft` vs add dedicated `/api/v1/drafts/{draft_id}/advisories` route. | Verified Fact / Open Question |
| **Draft Serialization Engine** | `app/api/serialization.py`<br>`serialize_draft` | Serializes draft metadata, `calculated_subtotal`, `header_provenance` (`customer_name`, `po_number`), and `line_items`. | Modify to serialize draft-level advisories and draft-level discrepancies. | **Integration Gap**: Does NOT currently serialize `OrderDraft.discrepancy_flags` at the top level, omitting order-level discrepancies. | Verified Fact / Integration Gap |
| **Discrepancy Serialization** | `app/api/serialization.py`<br>`_discrepancy` | Maps `DiscrepancyFlag` ORM instance to dict: `flag_id`, `discrepancy_type`, `severity`, `expected_value`, `requested_value`, `explanation`, `resolution_state`. | Can be reused directly to serialize top-level draft discrepancies. | **None**: Helper is complete and tested. | Verified Fact |
| **Line Serialization** | `app/api/serialization.py`<br>`_line` | Maps `DraftLineItem` to dict, embedding `discrepancies: [_discrepancy(flag)...]`. | Can embed line-specific advisory notices: `advisories: [_advisory(notice)...]`. | **Proposal**: Line-level advisories co-located with line discrepancies. | Verified Fact / Proposal |
| **Order-Level Discrepancy Gap** | `app/api/serialization.py`<br>`serialize_draft` vs `DiscrepancyFlag` | `DiscrepancyFlag` records with `line_item_id = None` (e.g. order-level `ArithmeticMismatch`) are stored in DB but omitted from `serialize_draft()` response. | Advisory must process `draft.discrepancy_flags` directly from DB, and draft serialization should be extended to return top-level `discrepancies`. | **CRITICAL INTEGRATION GAP**: Order-level discrepancies currently invisible in UI and API draft response! | Verified Fact / Critical Gap |
| **Header Provenance Gap** | `app/api/serialization.py`<br>`serialize_draft` vs `OrderDraft` | `OrderDraft.extracted_order_total_cents` exists in DB, but `serialize_draft` filters `header_provenance` strictly to `("customer_name", "po_number")`. | Advisory needs access to stated order total, and API should expose `extracted_order_total` in header provenance. | **Gap**: API projection omits stored order total provenance. | Verified Fact / Gap |
| **Approval Route & Gate** | `app/api/routes_drafts.py`<br>`approve_draft`<br>`order_service.py:approve_order` | POST `/api/v1/drafts/{draft_id}/approve`. Requires `status == "Ready for Approval"`. Fails with HTTP 409 (`DraftNotReadyForApprovalError`) if any unresolved discrepancy exists. | Advisory must NEVER alter status or allow un-reconciled orders to pass approval. | **Absolute Invariant**: Approval gate must remain 100% untouched. | Verified Fact |
| **UI Discrepancy Rendering** | `app/static/js/app.js`<br>`renderLine`<br>`renderDiscrepancy` | Renders `line.discrepancies` inside line table rows with badges for severity, type, and resolution state. No top-level discrepancy rendering. | Add rendering for line-level advisory notices and top-level draft advisory banner. | **UI Gap**: Current SPA has no component to render draft-level discrepancies or advisory cards. | Verified Fact / Gap |
| **Unit Test Suite** | `tests/unit/test_reconciliation.py` | Validates all 4 discrepancy categories, integer math, idempotency, and readiness invariants (AC1–AC13). | Model for new `tests/unit/test_symbolic_advisory.py` testing deterministic advisory rule catalog. | **Docstring Conflict**: Top docstring still claims T028 is unimplemented, whereas test code and `tasks.md` prove it is complete. | Verified Fact / Doc Contradiction |
| **Integration Test Suite** | `tests/integration/test_api_contracts.py`<br>`test_p2_mutation_routes.py` | End-to-end tests for intake, approval, catalog search, line mutations, and audit events. | Add integration tests verifying advisory output in draft responses and confirming zero regression on mutation contracts. | **None**: Solid existing test fixtures (`seeded_db`, `client`). | Verified Fact |

---

## 3. Deep Dive on Integration Mechanics

### 3.1 ORM Field Names vs JSON Projection Key Mapping

A critical requirement of this specification is distinguishing actual SQLite ORM column names from their serialized JSON representation in API responses.

#### Discrepancy Representation
| Dimension | ORM Field (`app/models/entities.py:DiscrepancyFlag`) | JSON Response Key (`app/api/serialization.py:_discrepancy`) | Format / Type |
|:---|:---|:---|:---|
| Primary Key | `id` | `flag_id` | UUID string |
| Parent Draft | `draft_id` | *(Omitted in `_discrepancy`)* | UUID string |
| Parent Line | `line_item_id` | *(Omitted in `_discrepancy`)* | UUID string or `None` |
| Discrepancy Category | `discrepancy_type` | `discrepancy_type` | String (`'PriceMismatch'`, etc.) |
| Severity | `severity` | `severity` | String (`'Blocking'`, `'Warning'`) |
| Expected Target | `expected_value` | `expected_value` | Formatted String (e.g. `"$25.00"`) |
| Source Document Value | `requested_value` | `requested_value` | Formatted String (e.g. `"$20.00"`) |
| Business Description | `explanation` | `explanation` | Text String |
| Resolution Status | `resolution_state` | `resolution_state` | String (`'Unresolved'`, etc.) |

#### Draft Header Representation
| Dimension | ORM Field (`OrderDraft`) | JSON Response Key (`serialize_draft`) | Format / Type |
|:---|:---|:---|:---|
| Draft ID | `id` | `draft_id` | UUID string |
| Source Document ID | `document_id` | `document_id` | UUID string |
| Customer ID | `customer_id` | `customer_id` | String (e.g. `"CUST-ACME"`) |
| Customer Name | `customer_name_extracted` | `customer_name_extracted` | String or `None` |
| PO Number | `po_number_extracted` | `po_number_extracted` | String or `None` |
| Lifecycle Status | `status` | `status` | String (`"Needs Review"`, etc.) |
| Replay Indicator | `is_replay_mode` | `is_replay_mode` | Boolean |
| Authoritative Subtotal | `calculated_subtotal_cents` | `calculated_subtotal` | Integer cents in DB; formatted Decimal string `"450.00"` in JSON |
| Customer Stated Total | `extracted_order_total_cents` | **OMITTED** | Integer cents in DB; **NOT exposed in JSON** |
| Top-level Discrepancies | `discrepancy_flags` | **OMITTED** | Relation in DB; **NOT exposed in JSON** |

---

## 3.2 Analysis of the Order-Level Serialization Gap

In `app/services/reconciliation.py:evaluate_clean_draft`, lines 341–357 implement order-level arithmetic evaluation:
```python
if draft.extracted_order_total_cents is not None:
    if not valid_money(draft.extracted_order_total_cents) or not all(
        valid_money(line.extracted_line_total_cents) for line in active_lines
    ):
        clean = False
    elif active_lines:
        source_order_total = calculate_subtotal_cents(
            line.extracted_line_total_cents for line in active_lines
        )
        if source_order_total != draft.extracted_order_total_cents:
            _ensure_unresolved_discrepancy(
                draft, None, "ArithmeticMismatch",
                _format_money(source_order_total), _format_money(draft.extracted_order_total_cents),
                "Customer-stated order total differs from the sum of customer-stated "
                "line totals for active lines.",
            )
```
When this check triggers, a `DiscrepancyFlag` is appended to `draft.discrepancy_flags` with `line_item=None` (`line_item_id=None`).

However, in `app/api/serialization.py`:
```python
def serialize_draft(draft: OrderDraft) -> dict:
    return {
        "draft_id": draft.id,
        ...
        "line_items": [_line(line) for line in sorted(...)],
    }
```
And in `_line(line)`:
```python
"discrepancies": [_discrepancy(flag) for flag in sorted(line.discrepancy_flags, ...)]
```

**The Gap Impact**:
1. Order-level flags are never attached to `line.discrepancy_flags`, only to `draft.discrepancy_flags`.
2. Because `serialize_draft` does not serialize `draft.discrepancy_flags` as a top-level field, client applications receiving `GET /api/v1/drafts/{draft_id}` receive a draft with `status = "Needs Review"`, but every line item shows an empty `discrepancies: []` array!
3. The operator has no visibility into why the draft is blocked unless they inspect server logs or database tables.
4. **Proposed 002 Remedy**:
   - Update `serialize_draft` to project top-level `discrepancies`:
     ```python
     "discrepancies": [_discrepancy(flag) for flag in sorted(draft.discrepancy_flags, ...) if flag.line_item_id is None]
     ```
   - Expose `advisories` at both the top level and line level.

---

### 3.3 Mutation Call Sites & Transaction Ownership

Symbolic Advisory must interact safely with existing mutation routes:

1. **`PATCH /api/v1/drafts/{draft_id}/lines/{line_id}` (`action="SelectSKU"`)**:
   - Handled in `app/api/routes_drafts.py:patch_line`.
   - Invokes `app/services/reconciliation.py:select_line_sku(db, draft, line, matched_sku=...)`.
   - `select_line_sku` updates `line.matched_sku`, `line.sku_resolution_source = "OPERATOR_SELECTED"`, marks flags `ResolvedByCorrection`, and calls `evaluate_clean_draft(db, draft)`.
   - `routes_drafts.py` adds `AuditEvent(event_type="SKUSelected")`, executes `db.flush()`, `db.commit()`, and returns `serialize_draft(updated_draft)`.
2. **`PATCH /api/v1/drafts/{draft_id}/lines/{line_id}` (`action="CorrectField"`)**:
   - Handled in `app/api/routes_drafts.py:patch_line`.
   - Invokes `app/services/reconciliation.py:correct_line_field(...)`.
   - Validates text grounding against `raw_text` (`SourceGroundingMismatchError` on failure).
   - Updates column, updates `FieldProvenance`, marks affected flags `ResolvedByCorrection`, re-evaluates draft via `evaluate_clean_draft`.
   - `routes_drafts.py` adds `AuditEvent(event_type="FieldCorrected")`, flushes, commits, and returns `serialize_draft(updated_draft)`.
3. **`DELETE /api/v1/drafts/{draft_id}/lines/{line_id}`**:
   - Handled in `app/api/routes_drafts.py:delete_line`.
   - Invokes `app/services/reconciliation.py:remove_line(...)`.
   - Updates `line.status = "Removed"`, marks flags `ResolvedByLineRemoval`, re-evaluates draft via `evaluate_clean_draft`.
   - `routes_drafts.py` adds `AuditEvent(event_type="LineRemoved")`, flushes, commits, and returns `serialize_draft(updated_draft)`.

**Transaction Invariant for Advisory**:
Neither the reconciliation engine nor the proposed advisory engine may issue `db.commit()` or `db.rollback()`. Transaction boundaries are strictly owned by the caller (`routes_drafts` or `order_service`).

---

### 3.4 Documentary Conflict Analysis

| Document | Stated Text / Assertion | Verified Runtime Reality on `main` | Resolution & Guidance |
|:---|:---|:---|:---|
| `docs/gates/04_p2_order_total_arithmetic_gate.md` | *"Production P2 discrepancy evaluation remains unimplemented pending T028."* (Section: Post-Decision Reconciliation Status) | T028 is fully implemented in `app/services/reconciliation.py` (lines 201–361). | **Historical Artifact**: Gate 04 was committed on 2026-10-01 before Vladimir implemented T028 in PR #19. The gate document was not retroactively updated. Runtime code on `main` is authoritative. |
| `tests/unit/test_reconciliation.py` (top docstring) | *"Defines the RED acceptance boundary prior to T028 implementation for: ... AC6 defines the genuine RED acceptance contract for order-level ArithmeticMismatch awaiting T028 implementation."* | All unit tests in `test_reconciliation.py` are passing GREEN. T028 is implemented. | **Historical Artifact**: The docstring preserves the test pack's initial RED design state. Tests are now regression assertions. |
| `specs/001-ordershield-po-reconciliation/tasks.md` | Tasks T026, T027, T028, T029, T030, T031, T032, T033, T034, T035, T036, T037, T038, T039, T040, T041 are marked `[x]`. | Matches runtime code on `main`. | Authoritative project task status. |
| `specs/001-ordershield-po-reconciliation/contracts/api-contracts.md` | Section 2.2 shows response sample without `extracted_order_total` in `header_provenance` and without top-level `discrepancies`. | Matches current `app/api/serialization.py`. | Confirms the documented integration gap between database models and API projection. |

---

## 4. Technical Safeguards & Invariant Checklist

To prevent regressions during future `002-symbolic-advisory` implementation, any pull request MUST verify the following invariants:

- [ ] **No Auto-Approvals**: `approve_order` must continue to reject any draft whose status is not `"Ready for Approval"` with HTTP 409 (`DraftNotReadyForApprovalError`).
- [ ] **No Direct Flag Manipulation**: Advisory code must not change `DiscrepancyFlag.resolution_state` from `"Unresolved"` to anything else.
- [ ] **No Raw Text Modification**: `PurchaseOrderDocument.raw_text` remains strictly immutable.
- [ ] **Pure In-Memory Math**: Any calculation of suggested quantities, package multiples, or price deltas must use pure integer cents or exact `Decimal` arithmetic.
- [ ] **Offline Execution**: The advisory engine must operate with zero network access and zero external API dependencies, passing pytest with the `no_external_network` fixture active.
