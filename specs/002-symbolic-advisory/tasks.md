# Tasks: 002-symbolic-advisory (Symbolic Advisory Layer)

**Input**: Design documents from `/specs/002-symbolic-advisory/` ([spec.md](spec.md), [integration-points.md](integration-points.md))  
**Prerequisites**: [spec.md](spec.md), [integration-points.md](integration-points.md), [.specify/memory/constitution.md](../../.specify/memory/constitution.md), [AGENTS.md](../../AGENTS.md)  
**Baseline Reference**: Feature 001 tasks T001–T041 are fully implemented and verified in `specs/001-ordershield-po-reconciliation/tasks.md`. New task numbering begins at **T042**.  
**Organization**: Tasks are grouped by phase and user story to enable incremental, test-driven delivery and clear traceability.

## Format: `[ID] [P?] [Story] Description`
- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (`[US1]`, `[US2]`, `[US3]`)
- **[PROPOSED/NEW]**: Marks newly introduced files, schemas, or symbols not yet in the codebase
- **blocked-on-decision**: Marks tasks that cannot be coded until explicit human decision gate sign-off is recorded

---

## Phase 1: Architecture & Requirements Decision Gates

**Purpose**: Formalize human decisions on API surface, serialization gaps, and data persistence before any implementation begins.

- [ ] T042 [blocked-on-decision] Human Decision Gate `HDG-SA-01`: Formalize and record decision between on-demand in-memory advisory derivation in `app/api/serialization.py` versus persistent SQLite storage (`AdvisoryNotice` entity in `app/models/entities.py`), in compliance with Constitution Principle III & V
- [ ] T043 [blocked-on-decision] Human Decision Gate `HDG-SA-02`: Formally approve API response contract extension in `specs/002-symbolic-advisory/spec.md` for `app/api/serialization.py:serialize_draft` to expose top-level draft discrepancies (`line_item_id is None`) and top-level advisory notices
- [ ] T044 [P] Define strict Pydantic schemas for advisory notices and action recommendations (`[PROPOSED/NEW] AdvisoryNoticeSchema`, `[PROPOSED/NEW] AdvisoryActionSchema`) in `app/models/schemas.py` supporting fields: `notice_id`, `scope` (`"draft"` | `"line"`), `line_number`, `discrepancy_type`, `rule_id`, `severity`, `title`, `explanation`, and `suggested_action`

---

## Phase 2: Test Harness & Acceptance Contracts (Tests First)

**Purpose**: Define executable RED acceptance contracts for the advisory engine, serialization gap closure, and regression safety prior to writing service logic.

- [ ] T045 [P] [US1] Unit test suite for packaging and pricing advisory rules in `[PROPOSED/NEW] tests/unit/test_symbolic_advisory.py` validating that `ADV-PKG-01` recommends exact upward/downward packaging increments satisfying MOQ, and `ADV-PRC-01` calculates quantity deltas required for volume discount tiers
- [ ] T046 [P] [US2] Unit test suite for arithmetic advisory rules in `[PROPOSED/NEW] tests/unit/test_symbolic_advisory.py` validating line-level arithmetic variance breakdowns (`ADV-ARITH-LINE-01`) and order-level customer stated total vs active line sum variance breakdown (`ADV-ARITH-ORDER-01`)
- [ ] T047 [P] [US1] Unit test suite for catalog ambiguity advisory rules in `[PROPOSED/NEW] tests/unit/test_symbolic_advisory.py` validating candidate SKU prioritization and base pricing guidance (`ADV-CAT-01`) for `CatalogMatchingMismatch` flags
- [ ] T048 [P] [US1] Integration contract test in `tests/integration/test_api_contracts.py` asserting that `GET /api/v1/drafts/{draft_id}` returns draft-level and line-level advisory notices, and exposes top-level order arithmetic discrepancies (`line_item_id=None`) when present

---

## Phase 3: Core Symbolic Advisory Service (Deterministic Rules)

**Purpose**: Implement the pure deterministic advisory engine that evaluates active discrepancy flags and domain context without side effects.

- [ ] T049 [US1] Implement core advisory coordinator function `evaluate_symbolic_advisories(db: Session, draft: OrderDraft) -> list[AdvisoryNoticeSchema]` in `[PROPOSED/NEW] app/services/symbolic_advisory.py` that inspects active `draft.discrepancy_flags`, queries catalog/contract data, and aggregates rule outputs
- [ ] T050 [P] [US1] Implement packaging and MOQ rule evaluator `evaluate_packaging_advisory(...)` (`ADV-PKG-01`) in `[PROPOSED/NEW] app/services/symbolic_advisory.py` computing nearest compliant multiples of `CatalogProduct.package_increment` and `min_order_quantity`
- [ ] T051 [P] [US1] Implement contract pricing tier rule evaluator `evaluate_price_tier_advisory(...)` (`ADV-PRC-01`) in `[PROPOSED/NEW] app/services/symbolic_advisory.py` inspecting all tiers in `CustomerContract` for the line's SKU and stating quantity thresholds for lower price tiers
- [ ] T052 [P] [US1] Implement catalog ambiguity evaluator `evaluate_catalog_advisory(...)` (`ADV-CAT-01`) in `[PROPOSED/NEW] app/services/symbolic_advisory.py` prioritizing candidates from `DraftLineItem.candidate_skus_json` and providing unit-of-measure / packaging constraints
- [ ] T053 [P] [US2] Implement arithmetic variance breakdown evaluator `evaluate_arithmetic_advisory(...)` (`ADV-ARITH-LINE-01`, `ADV-ARITH-ORDER-01`) in `[PROPOSED/NEW] app/services/symbolic_advisory.py` computing line product deltas and active line sum deltas against `OrderDraft.extracted_order_total_cents`

---

## Phase 4: API Integration & Serialization Gap Closure

**Purpose**: Expose advisory guidance and close the order-level discrepancy omission gap in API responses.

- [ ] T054 [US2] Close order-level discrepancy serialization gap in `app/api/serialization.py:serialize_draft` by projecting top-level draft discrepancy flags (`[flag for flag in draft.discrepancy_flags if flag.line_item_id is None]`) and including `extracted_order_total` in `header_provenance`
- [ ] T055 [US1] Extend `app/api/serialization.py:serialize_draft` and `app/api/serialization.py:_line` to invoke `evaluate_symbolic_advisories` (or format pre-computed advisories) and serialize structured `advisories` at both top-level draft and line levels
- [ ] T056 [US1] Ensure all draft mutation endpoints in `app/api/routes_drafts.py` (`patch_line`, `delete_line`) return updated advisory payloads through `serialize_draft` without altering existing transaction commit/rollback semantics

---

## Phase 5: UI Workspace Integration

**Purpose**: Surface explainable advisory guidance and top-level discrepancies in the operator browser workspace.

- [ ] T057 [P] [US1] Implement line-level advisory card rendering in `app/static/js/app.js` (inside `renderLine`) displaying rule badge, explanation, and recommended action next to line discrepancy flags
- [ ] T058 [P] [US2] Implement draft-level order summary advisory card in `app/static/js/app.js` (above line items table) rendering order-level arithmetic discrepancies and holistic PO advice
- [ ] T059 [P] [US1] Add CSS styles for advisory notices in `app/static/css/styles.css` (`.advisory-card`, `.advisory-badge`, `.advisory-action`) using clean, non-intrusive styling consistent with OrderShield UI design

---

## Phase 6: End-to-End Verification, Polish & Non-Regression

**Purpose**: Execute full verification suite, validate system invariants, and complete documentation.

- [ ] T060 [P] Implement end-to-end integration scenario test in `tests/integration/test_quickstart_scenarios.py` validating the full operator workflow: ingest discrepancy fixture -> inspect advisory recommendations -> perform grounded line correction -> verify draft transitions to `Ready for Approval`
- [ ] T061 Verify safety and invariant non-regression in `tests/integration/test_api_contracts.py`:
  - Assert that advisory generation NEVER mutates prices, quantities, or line records in SQLite
  - Assert that advisory generation NEVER alters `DiscrepancyFlag.resolution_state`
  - Assert that drafts with unresolved flags remain strictly blocked from approval (`approve_order` raises HTTP 409 `DraftNotReadyForApprovalError`)
- [ ] T062 [P] Update repository documentation in `README.md` and `docs/` describing the Symbolic Advisory layer, rule catalog, and operator guidance interface
- [ ] T063 Run repository verification commands (`python -m compileall app tests` and `pytest -v`) to confirm zero regressions and clean test execution

---

## Dependencies & Execution Order

### Phase Dependencies
- **Phase 1 (Decision Gates)**: Must complete first to unblock Phase 3 & 4.
- **Phase 2 (Tests First)**: Can be authored immediately based on proposed contracts in `spec.md`; MUST fail (RED) before Phase 3 implementation.
- **Phase 3 (Advisory Service)**: Depends on Phase 1 decisions and Phase 2 test specifications; implements core logic.
- **Phase 4 (API Serialization)**: Depends on Phase 3 service completion; wires advisory into HTTP layer.
- **Phase 5 (UI Integration)**: Depends on Phase 4 API completion.
- **Phase 6 (Verification & Non-Regression)**: Depends on Phase 4 & 5 completion.

### Parallel Opportunities (`[P]`)
- T044, T045, T046, T047, and T048 can be designed and drafted concurrently.
- Rule evaluators T050, T051, T052, and T053 can be developed concurrently once T049 core scaffolding is created.
- UI styling T059 can run in parallel with JavaScript rendering T057 and T058.
- End-to-end test T060 and documentation T062 can run in parallel.

---

## Acceptance Criteria & Quality Gates

Every task must satisfy:
1. **Zero Silent Fallback**: Advisory generation must never fall back to live AI or external network requests.
2. **Deterministic Output**: Evaluators must produce byte-for-byte identical output for identical database states.
3. **Immutability Protection**: Terminal drafts (`Approved` / `Rejected`) must not be recomputed or mutated.
4. **Offline Capability**: All unit and integration tests must pass with `no_external_network` fixture active.
