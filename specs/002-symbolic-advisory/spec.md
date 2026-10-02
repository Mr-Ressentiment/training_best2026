# Feature Specification: 002-symbolic-advisory

**Feature Branch**: `002-symbolic-advisory`  
**Base Branch**: `main` (canonical Phase 1–6 OrderShield implementation)  
**Created**: 2026-10-02  
**Status**: Draft / For Review  
**Input**: User description: "Prepare specification `002-symbolic-advisory`: study the current architecture and create a draft specification package for a new Symbolic Advisory feature over deterministic reconciliation results."

---

## 1. Executive Summary & Purpose

The OrderShield system currently evaluates customer purchase orders through a two-stage deterministic pipeline:
1. **Intake & Extraction**: An incoming document (`.txt` or `.pdf`) is parsed into canonical text (`app/services/document_parser.py`) and structured purchase order fields (`AIExtractionPayload` in `app/models/schemas.py`) via bounded AI inference (`LiveAIProvider`) or committed test fixtures (`FixtureAIProvider`), saving an `OrderDraft` and `DraftLineItem` entities into SQLite (`app/services/order_service.py:ingest_order`).
2. **Deterministic Reconciliation Engine**: The engine (`app/services/reconciliation.py:evaluate_clean_draft`) rigorously validates the draft against master catalog metadata (`CatalogProduct`) and customer contract pricing tiers (`ContractPriceTier`). Any discrepancy generates an unresolved blocker (`DiscrepancyFlag`) with `severity = "Blocking"` and locks the draft in `status = "Needs Review"`.

While the current engine reliably halts non-compliant orders, it only outputs raw discrepancy attributes:
- `discrepancy_type` (`PriceMismatch`, `QuantityOrPackagingBreach`, `ArithmeticMismatch`, `CatalogMatchingMismatch`)
- `expected_value`
- `requested_value`
- `explanation`

**The Problem**: Human operators encountering `Needs Review` drafts must manually inspect catalog tiers, minimum order quantities (MOQ), package increments, or customer calculation errors, formulate appropriate commercial remedies, verify whether proposed edits comply with source document grounding, and manually apply mutations (`SelectSKU`, `CorrectField`, `remove_line`). The current system provides no explainable guidance, suggested next actions, trade-off analysis, or pre-flight impact assessments.

**The Purpose of 002-symbolic-advisory**: Introduce a distinct, explainable **Symbolic Advisory layer** sitting directly on top of deterministic reconciliation results. The advisory layer analyzes active discrepancy flags, line items, customer contract tiers, and catalog constraints to produce actionable, human-readable recommendations and proposed operational remedies (such as suggested quantity adjustments to meet packaging increments, recommended catalog SKU candidates, or guidance on arithmetic variance causes).

---

## 2. Current Repository Facts (Authoritative Baseline)

Before designing the proposed 002 extension, the following operational and architectural facts are verified directly from the repository source code on `main`:

### 2.1 Deterministic Reconciliation Engine
- **Module & Primary Symbol**: `app/services/reconciliation.py:evaluate_clean_draft(db: Session, draft: OrderDraft) -> OrderDraft`
- **Arithmetic Helpers**:
  - `calculate_line_total_cents(quantity: int, contract_price_cents: int) -> int`: Exact integer cents math; fails closed on negative values, floats, or booleans.
  - `calculate_subtotal_cents(line_totals_cents: Iterable[int]) -> int`: Sums integer cents; empty yields 0 cents.
  - `select_contract_price_tier(db: Session, customer_id: str, sku: str, quantity: int) -> ContractPriceTier | None`: Selects tier with maximum `min_quantity <= quantity`. Fails closed (`PricingConflictError`) if tiers span multiple contracts. Returns `None` if no tier satisfies `min_quantity <= quantity` (no automatic fallback to base price or lower tiers).
- **Discrepancy Evaluation Rules**:
  - `PriceMismatch`: Evaluated when customer-stated `DraftLineItem.extracted_unit_price_cents` differs from authoritative contract tier unit price (`line.contract_price_cents = tier.tier_price_cents`).
  - `QuantityOrPackagingBreach`: Evaluated when `DraftLineItem.extracted_quantity` is less than `CatalogProduct.min_order_quantity` (MOQ) or not a whole multiple of `CatalogProduct.package_increment`.
  - Line-level `ArithmeticMismatch`: Evaluated when customer-stated line total (`line.extracted_line_total_cents`) differs from `calculate_line_total_cents(line.extracted_quantity, line.extracted_unit_price_cents)`.
  - Order-level `ArithmeticMismatch`: Evaluated when customer-stated order total (`OrderDraft.extracted_order_total_cents`) is present and differs from the sum of customer-stated line totals for active lines (`calculate_subtotal_cents(line.extracted_line_total_cents for line in active_lines)`).
    > **CRITICAL SEPARATION**: Order-level arithmetic strictly compares customer-stated order total against the sum of customer-stated line totals. It **must not** be confused with `OrderDraft.calculated_subtotal_cents` or contract-priced line totals (`line.calculated_line_total_cents`), which represent authoritative system pricing.
  - `CatalogMatchingMismatch`: Evaluated when `line.matched_sku` is absent, not found in `CatalogProduct`, or when `line.sku_confidence` is `Ambiguous` or `Unrecognized` without explicit `line.sku_resolution_source == "OPERATOR_SELECTED"`.

### 2.2 Reconciliation Call Sites & Mutation Architecture
- **Intake Pipeline**: `app/services/order_service.py:ingest_order(...)` extracts data, populates `OrderDraft` and `DraftLineItem`, persists `AuditEvent` records (`DocumentIngested`, `AIExtractionCompleted`), and calls `evaluate_clean_draft(db, draft)`. The transaction (flush/commit/rollback) is owned by `ingest_order`.
- **Operator Field Correction**: `app/services/reconciliation.py:correct_line_field(...)` validates that the proposed value and snippet are grounded in canonical `raw_text` (raising `SourceGroundingMismatchError` returning HTTP 422 on failure), updates field and provenance, marks affected flags as `ResolvedByCorrection`, and re-invokes `evaluate_clean_draft(db, draft)`.
- **Line Removal**: `app/services/reconciliation.py:remove_line(...)` marks line `status = "Removed"`, marks associated flags as `ResolvedByLineRemoval`, and re-invokes `evaluate_clean_draft(db, draft)`.
- **Operator SKU Selection**: `app/services/reconciliation.py:select_line_sku(...)` validates SKU in `CatalogProduct`, sets `sku_resolution_source = "OPERATOR_SELECTED"`, marks SKU-dependent flags as `ResolvedByCorrection`, and re-invokes `evaluate_clean_draft(db, draft)`.
- **Transaction Ownership**: In `correct_line_field`, `remove_line`, and `select_line_sku`, the caller (specifically `app/api/routes_drafts.py:patch_line` and `delete_line`) owns session flush, commit, rollback, and `AuditEvent` persistence (`FieldCorrected`, `LineRemoved`, `SKUSelected`).

### 2.3 Persisted Discrepancy Model
- **ORM Class**: `app/models/entities.py:DiscrepancyFlag`
- **Exact Schema Columns**:
  - `id`: `String` (UUID primary key)
  - `draft_id`: `String`, `ForeignKey("order_drafts.id")`, `nullable=False`
  - `line_item_id`: `String`, `ForeignKey("draft_line_items.id")`, `nullable=True` (strictly `None` for order-level discrepancies)
  - `discrepancy_type`: `String`, `nullable=False` (`CheckConstraint: 'PriceMismatch', 'QuantityOrPackagingBreach', 'ArithmeticMismatch', 'CatalogMatchingMismatch'`)
  - `severity`: `String`, `nullable=False`, default `"Blocking"` (`CheckConstraint: 'Blocking', 'Warning'`)
  - `expected_value`: `String`, `nullable=False`
  - `requested_value`: `String`, `nullable=False`
  - `explanation`: `Text`, `nullable=False`
  - `resolution_state`: `String`, `nullable=False`, default `"Unresolved"` (`CheckConstraint: 'Unresolved', 'ResolvedByCorrection', 'ResolvedByLineRemoval'`)
- **ORM Relationships**:
  - `OrderDraft.discrepancy_flags` (`relationship("DiscrepancyFlag", back_populates="draft")`, property alias: `.flags`)
  - `DraftLineItem.discrepancy_flags` (`relationship("DiscrepancyFlag", back_populates="line_item")`, property alias: `.flags`)

### 2.4 API Serialization & The Order-Level Discrepancy Integration Gap
- **Draft Retrieval Endpoint**: `app/api/routes_drafts.py:get_draft(...)` calls `app/api/serialization.py:serialize_draft(draft: OrderDraft)`.
- **Line Discrepancy Projection**: `app/api/serialization.py:_line(...)` serializes line items and embeds `discrepancies: [_discrepancy(flag) for flag in sorted(line.discrepancy_flags, ...)]`.
- **Documented Integration Gap**:
  1. `serialize_draft(draft)` constructs the top-level response dictionary with keys: `draft_id`, `document_id`, `customer_id`, `customer_name_extracted`, `po_number_extracted`, `status`, `rejection_reason`, `is_replay_mode`, `calculated_subtotal`, `header_provenance`, `line_items`.
  2. `serialize_draft(draft)` **does not** return a top-level `discrepancies` field for `OrderDraft.discrepancy_flags`.
  3. Consequently, order-level discrepancy flags (which have `line_item_id = None`, such as order-level `ArithmeticMismatch`) exist in the database and block draft readiness, but are **never exposed to API clients** in `GET /api/v1/drafts/{draft_id}`.
  4. Furthermore, `serialize_draft(draft)` only exposes `customer_name` and `po_number` in `header_provenance`, omitting `extracted_order_total` even though it is stored in `OrderDraft.extracted_order_total_cents`.

### 2.5 UI Rendering & Readiness Gating
- **UI Rendering**: `app/static/js/app.js:renderLine(line)` iterates over `line.discrepancies` and calls `renderDiscrepancy(flag)`. The SPA has no visual container or rendering logic for draft-level discrepancies.
- **Readiness Invariant**: `evaluate_clean_draft` sets `draft.status = "Ready for Approval"` only when `not any(flag.resolution_state == "Unresolved" for flag in draft.discrepancy_flags)`. Any unresolved flag forces `status = "Needs Review"`.
- **Approval Gate**: `app/services/order_service.py:approve_order(...)` requires `draft.status == "Ready for Approval"`, raising `DraftNotReadyForApprovalError` (HTTP 409 via `app/main.py:_not_ready_error`) on violation.

### 2.6 Historical Documentation Discrepancies
- In `specs/001-ordershield-po-reconciliation/tasks.md` on `main`, all tasks T001 through T041 are marked `[x]` (including T028 and T041 GO).
- However, the docstring of `tests/unit/test_reconciliation.py` and the gate record `docs/gates/04_p2_order_total_arithmetic_gate.md` preserve historical text stating that "Production P2 discrepancy evaluation remains unimplemented pending T028" and that AC6 defines the "RED acceptance contract awaiting T028".
- **Factual Resolution**: This is recorded as historical documentary residue. The actual codebase on `main` contains the fully implemented and passing T028 engine.

---

## 3. Safe Design Boundaries & Invariants

To maintain strict alignment with the OrderShield Constitution (`.specify/memory/constitution.md`) and AGENTS.md, the proposed Symbolic Advisory feature MUST adhere to the following invariants:

1. **Non-Authoritative Guidance**: Symbolic Advisory is strictly an auxiliary advisory layer. It is NOT a commercial source of truth.
2. **Zero Automated Mutation**: Symbolic Advisory MUST NOT alter line quantities, unit prices, line totals, customer identifiers, or document text.
3. **Zero Automated Discrepancy Resolution**: Symbolic Advisory MUST NOT modify `DiscrepancyFlag.resolution_state`, delete flags, or fabricate resolutions. All resolutions remain governed strictly by grounded operator actions (`correct_line_field`, `remove_line`, `select_line_sku`).
4. **Approval Gate Preservation**: Symbolic Advisory MUST NOT transition `OrderDraft.status` or bypass `DraftNotReadyForApprovalError`. An order draft with active unresolved discrepancies remains strictly blocked from approval.
5. **Deterministic Foundation**: Advisory rules MUST derive deterministically from verified repository artifacts (catalog data, contract tiers, discrepancy flags, and grounded line values). Advisory advice generation MUST NOT depend on unconstrained LLM hallucinations or opaque heuristics.

---

## 4. Proposed 002 Behavior: Symbolic Advisory [PROPOSED/NEW]

### 4.1 Architecture Overview
The proposed Symbolic Advisory layer will execute post-reconciliation:
```text
[Incoming PO]
      │
      ▼
[AI Extraction / Fixture Replay]
      │
      ▼
[Deterministic Reconciliation Engine: evaluate_clean_draft]
      │
      ├─► Generates DiscrepancyFlag records (Blocking / Unresolved)
      ├─► Sets Draft Status ("Needs Review" | "Ready for Approval")
      │
      ▼
[PROPOSED/NEW] [Symbolic Advisory Engine: evaluate_symbolic_advisories]
      │
      ├─► Reads: Active DiscrepancyFlags, DraftLineItems, CatalogProduct, ContractPriceTier
      ├─► Applies: Deterministic advisory rule catalog
      ├─► Emits: List of structured AdvisoryNotice objects (Draft-level & Line-level)
      │
      ▼
[Draft Inspection / UI Presentation]
      └─► Operator views discrepancy flags alongside explainable advisory guidance
```

### 4.2 Proposed Advisory Categories & Rule Catalog [PROPOSED/NEW]

The Symbolic Advisory engine proposes five core rule evaluators:

1. **Packaging & MOQ Advisory (`ADV-PKG-01`)**:
   - *Trigger*: `DiscrepancyFlag(discrepancy_type="QuantityOrPackagingBreach")`.
   - *Logic*: Calculate the nearest valid package increments:
     - Upward increment: `ceil(quantity / package_increment) * package_increment` (and `>= min_order_quantity`).
     - Downward increment (if valid): `floor(quantity / package_increment) * package_increment` (if `>= min_order_quantity`).
   - *Advice*: State exact recommended order quantities that fulfill MOQ and pack increment, and check whether bumping quantity qualifies for a higher customer contract discount tier.

2. **Contract Pricing Tier Advisory (`ADV-PRC-01`)**:
   - *Trigger*: `DiscrepancyFlag(discrepancy_type="PriceMismatch")`.
   - *Logic*: Compare customer-stated unit price against available tiers in `CustomerContract`.
   - *Advice*: Explain the exact tier boundaries (e.g., "Customer stated $24.00, but contract tier requires quantity >= 50 for $24.00; current quantity 20 qualifies only for tier $26.00"). Suggest whether increasing quantity to the next tier threshold would reconcile customer expectation.

3. **Catalog Ambiguity & Candidate Ranking Advisory (`ADV-CAT-01`)**:
   - *Trigger*: `DiscrepancyFlag(discrepancy_type="CatalogMatchingMismatch")`.
   - *Logic*: Inspect `DraftLineItem.candidate_skus_json` and `CatalogProduct`.
   - *Advice*: Explain why the item was flagged (missing SKU, low confidence, ambiguous candidate matches). Provide prioritized catalog matches with category, package rules, and base prices to guide operator SKU selection.

4. **Line-Level Arithmetic Breakdown Advisory (`ADV-ARITH-LINE-01`)**:
   - *Trigger*: `DiscrepancyFlag(discrepancy_type="ArithmeticMismatch", line_item_id!=None)`.
   - *Logic*: Compare `extracted_quantity * extracted_unit_price` vs `extracted_line_total`.
   - *Advice*: Identify whether the variance is due to apparent unit-price omission, round-trip transcription error, or discount deduction in customer line totals.

5. **Order-Level Arithmetic & Reconciliation Breakdown Advisory (`ADV-ARITH-ORDER-01`)**:
   - *Trigger*: `DiscrepancyFlag(discrepancy_type="ArithmeticMismatch", line_item_id=None)`.
   - *Logic*: Compare `OrderDraft.extracted_order_total_cents` vs `sum(active_line.extracted_line_total_cents)`.
   - *Advice*: Calculate the exact variance (`stated_total - sum_lines`). Analyze whether the difference corresponds to common unitemized charges (such as estimated shipping, pallet charges, or tax) and advise the operator to check source document headers before rejecting the order.

---

## 5. User Scenarios & Testing

### User Story 1 - Operator Receives Explainable Packaging & Price Remedies (Priority: P1) 🎯 MVP

As an order intake operator reviewing a purchase order blocked in `Needs Review` due to packaging and pricing discrepancies,  
I want the system to provide explainable advisory notices with exact recommended quantities and applicable contract pricing tiers,  
So that I can quickly understand the commercial discrepancy and make grounded corrections without manual catalog calculations.

**Why this priority**: Packaging and price mismatches represent the majority of day-to-day wholesale discrepancies. Providing instant arithmetic breakdowns and suggested quantities yields immediate operator efficiency.

**Independent Test**: Can be verified by ingesting an order with an invalid package quantity (e.g. 7 units when package increment is 5), verifying that `evaluate_symbolic_advisories` produces an advisory notice recommending 10 units, and confirming that the advisory does not mutate the draft.

**Acceptance Scenarios**:
1. **Given** an active line item with quantity 7, MOQ 5, and package increment 5,  
   **When** reconciliation generates a `QuantityOrPackagingBreach` flag,  
   **Then** the advisory engine generates an `AdvisoryNotice` indicating that 10 units is the minimum compliant quantity satisfying packaging rules.
2. **Given** an active line item with customer unit price $22.00,  
   **When** contract pricing tier 1 (qty 1-19) is $25.00 and tier 2 (qty 20+) is $22.00, and current quantity is 15,  
   **Then** the advisory engine generates an `AdvisoryNotice` explaining that the requested $22.00 price requires 5 additional units to qualify for Tier 2.

---

### User Story 2 - Order-Level Arithmetic Guidance & Gap Remediation (Priority: P2)

As an order intake operator reviewing an order with an order-level total discrepancy,  
I want the system to surface order-level advisory guidance explaining the difference between the customer-stated order total and active line item totals,  
So that I can determine whether the customer document includes unitemized freight, taxes, or line omission.

**Why this priority**: Resolves the existing integration gap where order-level discrepancies (`line_item_id = None`) are currently omitted from draft serialization and invisible in the UI.

**Independent Test**: Ingest a purchase order where stated order total is $550.00 but sum of line totals is $500.00; verify that the draft inspection payload includes the order-level advisory with a $50.00 variance explanation.

**Acceptance Scenarios**:
1. **Given** an order draft with stated order total $550.00 and active line sum $500.00,  
   **When** the draft is retrieved via API,  
   **Then** the response includes a draft-level advisory identifying a +$50.00 customer variance and suggesting verification of freight/tax clauses in source text.
2. **Given** an order draft with no stated order total (`extracted_order_total_cents = None`),  
   **When** the advisory engine runs,  
   **Then** no order-level arithmetic advisory is fabricated.

---

### User Story 3 - Pre-flight What-If Analysis for Operator Actions (Priority: P3) [NEEDS CLARIFICATION]

As an order intake operator considering an edit or SKU selection,  
I want to preview the outcome of an advisory recommendation before applying it,  
So that I can confirm whether the proposed change will fully resolve the draft into `Ready for Approval`.

**Why this priority**: Advanced enhancement providing interactive simulation; lower priority than baseline advisory generation.

**Independent Test**: Invoke a dry-run / preflight endpoint with proposed line edits and verify that expected remaining flags are returned without mutating database state.

**Acceptance Scenarios**:
1. **Given** a draft in `Needs Review` with one packaging breach,  
   **When** the operator requests a preflight check for quantity 10,  
   **Then** the system returns simulated status `Ready for Approval` with 0 unresolved flags, leaving persisted database state unmutated.

---

## 6. Edge Cases & Boundary Conditions

1. **Clean Order Draft**: When a draft has zero discrepancies and status is `Ready for Approval`, the advisory engine must return an empty advisory list (or an informational "Ready for Approval" confirmation) with zero overhead.
2. **Multiple Competing Discrepancies on Single Line**: If a line item suffers both `CatalogMatchingMismatch` and `PriceMismatch`, the advisory engine MUST prioritize catalog SKU resolution first, noting that contract pricing cannot be evaluated until SKU identity is established.
3. **Missing Customer Contract**: If customer ID has no contract in `CustomerContract`, the advisory engine must explicitly report that commercial contract lookup failed and suggest operator verification of customer account onboarding.
4. **Terminal Drafts (Approved / Rejected)**: Terminal drafts are immutable. Advisory inspection for terminal drafts must return historical advisory state without attempting re-evaluation.
5. **PDF vs TXT Grounding Awareness**: When recommending field corrections, advisory texts MUST explicitly remind operators that corrections must be strictly grounded in verbatim document text per `SourceGroundingMismatchError`.

---

## 7. Functional Requirements

- **SA-FR-001**: The system MUST implement a distinct Symbolic Advisory engine (`[PROPOSED/NEW] app/services/symbolic_advisory.py:evaluate_symbolic_advisories`) that executes after deterministic reconciliation.
- **SA-FR-002**: Symbolic Advisory MUST be completely deterministic and reproducible. It MUST NOT make unconstrained LLM calls or network requests during advisory generation.
- **SA-FR-003**: Symbolic Advisory MUST NOT mutate `OrderDraft`, `DraftLineItem`, `DiscrepancyFlag`, `CustomerContract`, or `CatalogProduct` database records.
- **SA-FR-004**: Symbolic Advisory MUST NOT alter `OrderDraft.status` or bypass `DraftNotReadyForApprovalError` during order approval.
- **SA-FR-005**: For any `QuantityOrPackagingBreach` flag, the advisory engine MUST compute and return the nearest compliant upper quantity fulfilling both MOQ and package increment.
- **SA-FR-006**: For any `PriceMismatch` flag, the advisory engine MUST evaluate all contract tiers for that customer and SKU, reporting the required threshold quantity to attain the customer-stated price if such a tier exists.
- **SA-FR-007**: For any line-level `ArithmeticMismatch` flag, the advisory engine MUST provide the mathematical product of extracted quantity and unit price, highlighting the exact dollar difference from the customer-stated line total.
- **SA-FR-008**: For any order-level `ArithmeticMismatch` flag, the advisory engine MUST compute the mathematical sum of active line totals and state the exact delta relative to `OrderDraft.extracted_order_total_cents`.
- **SA-FR-009**: The draft serialization contract (`app/api/serialization.py:serialize_draft`) MUST be extended to include top-level draft discrepancies and advisory notices [PROPOSED/NEW: requires Human Decision Gate].
- **SA-FR-010**: All advisory outputs MUST conform to a strict Pydantic contract (`[PROPOSED/NEW] AdvisoryNoticeSchema`), including unique notice ID, target scope (`draft` or `line`), rule identifier, severity, human-readable advice, and suggested action payload.
- **SA-FR-011**: [NEEDS CLARIFICATION] Advisory notices MAY be stored in a dedicated database table (`advisory_notices`) OR computed on-the-fly during serialization.
- **SA-FR-012**: [NEEDS CLARIFICATION] The UI (`app/static/js/app.js`) MUST render advisory notices in a dedicated section in the draft inspection workspace.

---

## 8. Success Criteria

- **SC-001**: 100% deterministic reproducibility: Identical draft and catalog states must produce identical advisory outputs across repeated invocations.
- **SC-002**: Zero unauthorized mutations: Advisory execution must make 0 database modifications to commercial values or discrepancy states.
- **SC-003**: 100% preservation of approval integrity: Passing through advisory evaluation must never allow an un-reconciled draft with unresolved flags to be approved.
- **SC-004**: Execution overhead under 15ms for a 10-line purchase order with multiple discrepancies.

---

## 9. Open Questions & Human Decision Gates

The following items are underspecified and require explicit human decision gates before implementation begins:

1. **`HDG-SA-01`: Storage Strategy (Persisted ORM Entity vs On-the-Fly Derivation)**:
   - *Option A (On-the-Fly)*: Compute advisories dynamically inside `serialize_draft(...)` or a dedicated GET endpoint without modifying the database schema.
   - *Option B (Persisted Entity)*: Create an `AdvisoryNotice` SQLAlchemy model in `app/models/entities.py` and persist notices during reconciliation.
   - *Recommendation*: Option A avoids schema migrations, maintains lean persistence, and complies with Principle V (Vertical Slice Simplicity).

2. **`HDG-SA-02`: API Surface for Advisories & Order-Level Discrepancies**:
   - *Option A*: Extend existing `GET /api/v1/drafts/{draft_id}` response in `app/api/serialization.py` to add `advisories: list` and `discrepancies: list` (top-level draft flags).
   - *Option B*: Create a dedicated read-only endpoint `GET /api/v1/drafts/{draft_id}/advisories`.
   - *Recommendation*: Option A closes the existing order-level discrepancy gap while keeping draft inspection cohesive in a single network round-trip.

3. **`HDG-SA-03`: UI Placement in SPA Workspace**:
   - *Open Question*: Should advisory notices appear inside each line item table row, inside a dedicated "Advisory Assistant" side drawer, or as an alert banner above the line items table?
   - *Status*: `[NEEDS CLARIFICATION]` from product lead.

4. **`HDG-SA-04`: Interactive Quick-Fix Actionability**:
   - *Open Question*: Should advisory recommendations include one-click "Apply" buttons in the UI that dispatch existing `PATCH /api/v1/drafts/{id}/lines/{id}` requests?
   - *Status*: `[NEEDS CLARIFICATION]`. If permitted, actions must strictly call existing grounded endpoints and cannot bypass grounding validation.

---

## 10. Traceability & Related Documents

- **Integration Points Mapping**: [integration-points.md](integration-points.md)
- **Implementation Backlog**: [tasks.md](tasks.md)
- **Canonical Feature 001 Baseline**:
  - Specification: `specs/001-ordershield-po-reconciliation/spec.md`
  - Data Model: `specs/001-ordershield-po-reconciliation/data-model.md`
  - API Contracts: `specs/001-ordershield-po-reconciliation/contracts/api-contracts.md`
  - Implementation Tasks: `specs/001-ordershield-po-reconciliation/tasks.md`
- **Architectural & Gate Documents**:
  - Order-Total Gate: `docs/gates/04_p2_order_total_arithmetic_gate.md`
  - Project Constitution: `.specify/memory/constitution.md`
  - Agent Guidelines: `AGENTS.md`
