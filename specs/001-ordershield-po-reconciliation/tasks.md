# Tasks: OrderShield Purchase Order Reconciliation

**Input**: Design documents from `/specs/001-ordershield-po-reconciliation/`  
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/api-contracts.md](contracts/api-contracts.md), [quickstart.md](quickstart.md), [ADR 0001](../../docs/decisions/0001-training-live-ai-provider-selection.md), [.specify/memory/constitution.md](../../.specify/memory/constitution.md), [AGENTS.md](../../AGENTS.md)  
**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.
**Status**: Implemented & Verified (T041 GO)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (`[US1]`, `[US2]`, `[US3]`)
- Include exact file paths in every task description

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization, dependency management, and baseline test harnesses

- [x] T001 Initialize repository directory structure for backend, frontend assets, tests, and fixtures per implementation plan in `app/`, `app/api/`, `app/models/`, `app/services/`, `app/static/css/`, `app/static/js/components/`, `tests/fixtures/`, `tests/unit/`, and `tests/integration/`
- [x] T002 [P] Configure Python 3.11+ application dependencies in `requirements.txt` (`fastapi`, `uvicorn`, `pydantic>=2.0`, `pypdf`, `sqlalchemy`, `pytest`, `httpx`)
- [x] T003 [P] Configure application runtime and environment settings in `app/config.py` and create committed `.env.example` with non-secret placeholders (`LLM_PROVIDER="qwen"`, `LLM_API_KEY=""`, `DATABASE_URL="sqlite:///ordershield.db"`, `LIVE_INFERENCE_TIMEOUT=15.0`, `IMMEDIATE_FAILURE_TIMEOUT=5.0`); enforce that replay mode is entered only through dedicated `/api/v1/fixtures/...` routes and that no configuration flag or setting may redirect live `/api/v1/orders/ingest` traffic to `FixtureAIProvider`
- [x] T004 [P] Configure pytest test harness and database fixtures in `tests/conftest.py` with in-memory SQLite (`sqlite:///:memory:`), FastAPI `TestClient`, and mock provider helpers

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core data layer, models, deterministic decimal math, document parser, and fixtures that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T005 Setup SQLite database connection engine, session factory, and foreign key pragma enforcement (`PRAGMA foreign_keys = ON`) in `app/database.py`
- [x] T006 Implement core master catalog and contract SQLAlchemy ORM models in `app/models/entities.py` for `CatalogProduct` (quoting constraints: `sku` String PK, `name` String required, `category` String required, `unit_of_measure` String required, `base_price_cents` Integer required >= 0, `min_order_quantity` Integer required >= 1, `package_increment` Integer required >= 1), `CustomerContract` (`id` String PK UUID, `customer_id` String required indexed, `customer_name` String required, `valid_from` Date required, `valid_to` Date required), and `ContractPriceTier` (`id` String PK UUID, `contract_id` String FK, `sku` String FK, `min_quantity` Integer required >= 1, `tier_price_cents` Integer required >= 0)
- [x] T007 Implement reconciliation domain SQLAlchemy ORM models in `app/models/entities.py` (following T006 sequentially in same file) for `PurchaseOrderDocument` (`id` String PK UUID, `filename` String required, `content_type` String required enum `text/plain` | `application/pdf`, `raw_text` Text required canonical source, `status` String enum `Ingested` | `Failed`, `ingested_at` DateTime UTC), `OrderDraft` (`id` String PK UUID, `document_id` String FK, `customer_id` String nullable, `customer_name_extracted` String nullable, `po_number_extracted` String nullable, `status` String enum `Ingested` | `Needs Review` | `Ready for Approval` | `Approved` | `Rejected`, `calculated_subtotal_cents` Integer default 0, `extracted_order_total_cents` Integer nullable default None, `rejection_reason` String nullable, `is_replay_mode` Boolean default false, `created_at` / `updated_at` DateTime), `DraftLineItem` (`id` String PK UUID, `draft_id` String FK, `line_number` Integer >= 1, `customer_description` String required, `extracted_quantity` Integer nullable, `extracted_unit_price_cents` Integer nullable, `extracted_line_total_cents` Integer nullable, `matched_sku` String FK nullable, `sku_confidence` String enum `High` | `Ambiguous` | `Unrecognized` nullable, `sku_resolution_source` String enum `NONE` | `AI_HIGH_CONFIDENCE` | `OPERATOR_SELECTED` default `NONE`, `candidate_skus_json` Text nullable, `matching_rationale` String nullable, `contract_price_cents` Integer nullable, `calculated_line_total_cents` Integer default 0, `status` String enum `Active` | `Removed` default `Active`), `FieldProvenance` (`id` String PK UUID, `draft_id` String FK, `line_item_id` String FK nullable, `field_name` String required including `extracted_order_total`, `verbatim_snippet` Text required, `location_type` String enum `txt` | `pdf`, `location_data_json` Text required), `DiscrepancyFlag` (`id` String PK UUID, `draft_id` String FK, `line_item_id` String FK nullable, `discrepancy_type` String enum `PriceMismatch` | `QuantityOrPackagingBreach` | `ArithmeticMismatch` | `CatalogMatchingMismatch`, `severity` String enum `Blocking` | `Warning` default `Blocking`, `expected_value` String required, `requested_value` String required, `explanation` Text required, `resolution_state` String enum `Unresolved` | `ResolvedByCorrection` | `ResolvedByLineRemoval` default `Unresolved`), `VerifiedOrderRecord` (`id` String PK UUID, `draft_id` String FK unique, `order_number` String required unique, `customer_id` String required, `po_number` String required, `grand_total_cents` Integer required, `approved_by` String required, `approved_at` DateTime UTC, `is_replay_mode` Boolean required, `line_items_snapshot_json` Text required), and `AuditEvent` (`id` String PK UUID, `draft_id` String FK, `event_type` String required, `actor` String required, `details_json` Text required, `timestamp` DateTime UTC)
- [x] T008 [P] Implement Pydantic v2 domain schemas and exact integer cents / decimal money mapping (`Decimal("25.00")` <-> `2500` cents) in `app/models/schemas.py`, including untrusted AI extraction schemas (`AIExtractionPayload` with optional `extracted_order_total`, `AILineItemPayload`), provenance schemas (`FieldProvenanceSchema`, `LocationDataSchema`, `HeaderProvenanceSchema` with optional `extracted_order_total`), and standard API error schemas (`ErrorResponse`, `SourceGroundingMismatchError`, `TerminalDraftConflictError`)
- [x] T009 [P] Implement pure-Python document extraction service in `app/services/document_parser.py` supporting UTF-8 plain text (`.txt`) and digital PDF text streams via `pypdf`, recording canonical `raw_text` and char/line offset locations, and raising immediate `UnextractableTextError` without OCR fallback when extractable text is absent
- [x] T010 Implement deterministic contract pricing tier lookup and integer cents arithmetic engine in `app/services/reconciliation.py` (querying tiers where `contract.customer_id == C`, `sku == S`, and `min_quantity <= Q`, selecting maximum `min_quantity`, calculating `extracted_quantity * contract_price_cents`, and subtotal sums)
- [x] T011 [P] Implement SQLite database initialization and baseline catalog / customer contract seeding CLI commands (`init-db --seed`) in `app/cli.py` pre-loading 10–30 catalog products and customer contract pricing tiers
- [x] T012 [P] Create synthetic purchase order test documents in `tests/fixtures/po_clean_acme.txt`, `tests/fixtures/po_discrepancy_apex.txt`, `tests/fixtures/po_ambiguous_apex.txt`, and `tests/fixtures/po_unextractable.pdf`, and committed pre-verified runtime fixture datasets in `app/fixtures/` (`app/fixtures/clean_acme.json`, `app/fixtures/discrepancy_apex.json`, `app/fixtures/ambiguous_apex.json`) whose extraction bodies conform exactly to the application-owned `AIExtractionPayload` schema (keeping replay/fixture metadata outside the extraction payload body, with `FixtureAIProvider` and `order_service` setting `is_replay_mode=true` on the resulting `OrderDraft`)

**Checkpoint**: Foundation ready - data models, parser, decimal pricing engine, CLI seeding, and fixtures are fully operational.

---

## Phase 3: User Story 1 - End-to-End Clean Purchase Order Intake & Verification (Priority: P1) 🎯 MVP

**Goal**: Full working vertical slice for clean PO intake: parse incoming document (`.txt`, `.pdf`), extract mandatory headers, optional stated order total, and line items via `OrderShieldAIProvider` (Alibaba Qwen 3.8 Flash primary with reasoning disabled, Google Gemini 3.5 Flash-Lite fallback candidate, and fixture replay provider), evaluate contract price tiers deterministically, verify 0 discrepancies, present a `Ready for Approval` draft with provenance citations, atomically approve order to create immutable `VerifiedOrderRecord`, enforce terminal state protection (`409 Conflict`), and provide an offline-capable single-page UI workspace.

**Independent Test**: Ingest `tests/fixtures/po_clean_acme.txt` via `POST /api/v1/orders/ingest` (or via fixture intake `POST /api/v1/fixtures/fixture-clean-acme/ingest`). Verify header extraction (`CUST-ACME`, `PO-10023`), high-confidence SKU mapping (`sku_resolution_source = "AI_HIGH_CONFIDENCE"`), contract pricing tier evaluation, 0 discrepancies, status `Ready for Approval`, atomic approval creating `VerifiedOrderRecord` `VO-2026-0001`, and subsequent mutation returning `409 Conflict`.

### Tests for User Story 1 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T013 [P] [US1] Unit tests for document parser in `tests/unit/test_document_parser.py` validating UTF-8 plain text extraction, digital PDF extraction via `pypdf`, canonical `raw_text` preservation, location offset generation (`type: "txt"`, `line_number`, `char_offset`), and immediate `UnextractableTextError` for unextractable PDFs
- [x] T014 [P] [US1] Unit tests for deterministic contract tier pricing and decimal arithmetic in `tests/unit/test_pricing_tiers.py` validating quantity threshold selection (max `min_quantity <= Q`), integer cents conversions, absence of floating-point drift, and fallback when quantity falls below all tiers
- [x] T015 [P] [US1] Unit tests for `OrderShieldAIProvider` boundary and failure semantics in `tests/unit/test_ai_provider.py` using controlled mocks/fakes (not real provider timing) to validate untrusted Pydantic schema validation, bounded ≤15s client timeout abortion, target ≤5s immediate diagnostic failure handling on auth/connection errors, zero automatic provider failover, and zero fixture substitution (real-provider smoke testing remains manual/live verification)
- [x] T016 [P] [US1] Integration tests for clean PO intake and atomic approval lifecycle in `tests/integration/test_api_contracts.py` validating `POST /api/v1/orders/ingest`, `GET /api/v1/drafts/{draft_id}`, `POST /api/v1/drafts/{draft_id}/approve`, terminal state immutability (`409 Conflict` on repeated approval or edits), zero partial draft persistence on live inference failure, replay isolation via `POST /api/v1/fixtures/{fixture_id}/ingest` with `"is_replay_mode": true`, and automated assertions that `AuditEvent.details_json` records exact active provider/model for live intake and fixture/non-live provenance for replay

### Implementation for User Story 1

- [x] T017 [US1] Implement pluggable `OrderShieldAIProvider` interface and concrete providers in `app/services/ai_provider.py` with `LiveAIProvider` connecting to Alibaba Qwen 3.8 Flash (reasoning disabled) via OpenAI-compatible endpoint with explicit Google Gemini 3.5 Flash-Lite configuration support, enforcing untrusted Pydantic schema validation, bounded ≤15s timeout, target ≤5s immediate diagnostic error handling, zero automatic runtime failover, zero silent fixture fallback, recording exact active provider and model metadata (`"qwen"`/`"qwen3.8-flash"` or `"gemini"`/`"gemini-3.5-flash-lite"`), and `FixtureAIProvider` loading pre-verified datasets from `app/fixtures/` with explicit non-live fixture provenance (`"fixture"`/`"pre-verified-dataset"`) and `is_replay_mode=true`
- [x] T018 [US1] Implement clean PO reconciliation and draft readiness evaluation in `app/services/reconciliation.py` validating presence of mandatory header fields (`customer_name`, `po_number`), high-confidence SKU mapping (`sku_resolution_source = "AI_HIGH_CONFIDENCE"`), contract tier price matching, integer cents line/subtotal calculation, and transition to `Ready for Approval` when 0 discrepancies exist
- [x] T019 [US1] Implement high-level order intake and atomic approval coordinator in `app/services/order_service.py` orchestrating document intake, draft creation with header and line `FieldProvenance` records (including grounding and cents conversion for optional `extracted_order_total`), recording exact active provider/model in `AuditEvent.details_json` for live intake (e.g. `details_json='{"provider": "qwen", "model": "qwen3.8-flash"}'`) and fixture/non-live provenance for replay (e.g. `details_json='{"provider": "fixture", "model": "pre-verified-dataset"}'`), atomic approval in a single database transaction (verifying `Ready for Approval`, generating `VerifiedOrderRecord` with unique `draft_id`, updating draft status to `Approved`, emitting `AuditEvent`), and duplicate approval prevention (`409 Conflict`)
- [x] T020 [P] [US1] Implement live document intake route (`POST /api/v1/orders/ingest`) in `app/api/routes_orders.py` and demo fixture intake routes (`GET /api/v1/fixtures`, `POST /api/v1/fixtures/{fixture_id}/ingest`) in `app/api/routes_fixtures.py`, enforcing live/replay separation, error response mapping (HTTP 400 for unextractable text, HTTP 502 for AI validation failure, HTTP 503 for provider timeout/failure), and explicit `"is_replay_mode"` tagging
- [x] T021 [P] [US1] Implement draft inspection (`GET /api/v1/drafts/{draft_id}`) and atomic approval (`POST /api/v1/drafts/{draft_id}/approve`) routes in `app/api/routes_drafts.py`, returning HTTP 409 Conflict if draft is not in `Ready for Approval` or is already in terminal state
- [x] T022 [US1] Implement FastAPI application factory, middleware, exception handlers, and static routing in `app/main.py` mounting routers, configuring error status responses, and serving offline static assets from `app/static/`
- [x] T023 [P] [US1] Implement offline-capable Single Page Application HTML workspace structure in `app/static/index.html` with drag-and-drop document upload, draft summary header cards, line-item reconciliation table, approval action bar, and unclosable replay banner container
- [x] T024 [P] [US1] Implement committed local CSS styling in `app/static/css/styles.css` with responsive layout, status badges (`Ready for Approval` green badge, `Needs Review` amber badge), and replay banner styling (`.banner-nonlive`) without external CDN dependencies
- [x] T025 [US1] Implement Single Page Application frontend logic in `app/static/js/app.js` managing live and replay file ingestion, draft state rendering, line item table display, "Approve Order" action submission, replay banner toggle based on `is_replay_mode`, and diagnostic error banner display

**Checkpoint**: At this point, User Story 1 (P1 MVP) is completely functional and independently demonstrable end-to-end.

---

## Phase 4: User Story 2 - Automated Discrepancy Detection & Ambiguity Routing (Priority: P2)

**Goal**: Automatically detect and flag all 4 supported MVP discrepancy categories (`PriceMismatch`, `QuantityOrPackagingBreach`, `ArithmeticMismatch`, `CatalogMatchingMismatch`), block premature approval, enable candidate SKU selection, provide master catalog search (`GET /api/v1/catalog`), enforce server-side grounding validation on field corrections (`CorrectField` vs `raw_text`), preserve discrepancy history upon line removal (`ResolvedByLineRemoval`), and support draft rejection with mandatory recorded reason.

**Independent Test**: Ingest `tests/fixtures/po_discrepancy_apex.txt` containing a price mismatch and an ambiguous description. Verify draft enters `Needs Review` with approval disabled. Execute candidate SKU selection (`SelectSKU` with `SKU-WRAP-15`) and verify line updates to `sku_resolution_source = "OPERATOR_SELECTED"`. Verify deterministic revalidation exposes the genuine `QuantityOrPackagingBreach` because source quantity 2 is below MOQ 5 (forging quantity corrections remains prohibited). Remove the non-compliant price-mismatch line and verify its discrepancy transitions to `ResolvedByLineRemoval`. Verify the remaining unresolved MOQ breach keeps the draft in `Needs Review` and approval remains blocked. Reject the draft with mandatory operator identity and reason (`POST /api/v1/drafts/{draft_id}/reject`) and verify transition to terminal `Rejected` state with zero verified order records.

### Tests for User Story 2 ⚠️

- [x] T026 [P] [US2] Unit tests for discrepancy evaluation engine in `tests/unit/test_reconciliation.py` validating detection of `PriceMismatch` (stated price != tier price), `QuantityOrPackagingBreach` (quantity < MOQ or non-multiple of packaging increment), `ArithmeticMismatch` (line total != qty * price or order total != sum of customer-stated line totals, evaluated independently from contract pricing), `CatalogMatchingMismatch` (ambiguous or unrecognized SKU), and verifying that unresolved discrepancies block `Ready for Approval`
- [x] T027 [P] [US2] Integration tests for operator discrepancy resolution workflows in `tests/integration/test_api_contracts.py` validating candidate SKU selection (`PATCH .../lines/{line_id}` with `action="SelectSKU"`), master catalog search (`GET /api/v1/catalog?query=...`), server-side grounding validation (`PATCH .../lines/{line_id}` with `action="CorrectField"` returning HTTP 422 on mismatch with `raw_text`), line removal (`DELETE .../lines/{line_id}` preserving discrepancies as `ResolvedByLineRemoval`), draft rejection (`POST .../reject` with mandatory reason), and terminal state protection (`409 Conflict`)

### Implementation for User Story 2

- [x] T028 [US2] Extend reconciliation engine in `app/services/reconciliation.py` to evaluate the 4 supported discrepancy categories, create `DiscrepancyFlag` records with expected/requested values and explanations, transition draft status to `Needs Review` whenever unresolved discrepancies exist, and enforce `sku_resolution_source IN ('AI_HIGH_CONFIDENCE', 'OPERATOR_SELECTED')` on all active lines
- [x] T029 [US2] Implement server-side grounded field correction and line removal logic in `app/services/reconciliation.py` validating `CorrectField` against canonical `PurchaseOrderDocument.raw_text` (raising `SourceGroundingMismatchError` returning HTTP 422 if value/snippet does not match canonical span), updating line status to `Removed` on deletion, and transitioning associated unresolved discrepancies to `resolution_state = "ResolvedByLineRemoval"` without deleting records
- [x] T030 [P] [US2] Implement master catalog lookup API route in `app/api/routes_catalog.py` (`GET /api/v1/catalog`) supporting keyword search across SKU code and product name and category filtering
- [x] T031 [P] [US2] Implement line item mutation and draft rejection routes in `app/api/routes_drafts.py` supporting `PATCH /api/v1/drafts/{draft_id}/lines/{line_id}` (handling `SelectSKU` and `CorrectField`), `DELETE /api/v1/drafts/{draft_id}/lines/{line_id}` (line removal), and `POST /api/v1/drafts/{draft_id}/reject` (requiring mandatory `operator_id` and `reason`), with terminal state protection (`409 Conflict`)
- [x] T032 [US2] Extend order coordinator in `app/services/order_service.py` to process draft rejection, recording `DraftRejected` in `AuditEvent`, transitioning draft status to `Rejected`, and locking the draft against further mutation
- [x] T033 [P] [US2] Implement catalog search modal component in `app/static/js/components/catalog_modal.js` enabling operators to search master catalog SKUs via `GET /api/v1/catalog`, inspect product details, and select a SKU to resolve ambiguous or unrecognized items
- [x] T034 [US2] Extend SPA UI in `app/static/js/app.js` and `app/static/css/styles.css` to render discrepancy warning badges, candidate SKU selection dropdowns, line deletion button with subtotal recalculation, rejection modal with mandatory reason, and disable "Approve Order" button while draft is in `Needs Review`

**Checkpoint**: At this point, User Stories 1 AND 2 are fully functional and testable independently.

---

## Phase 5: User Story 3 - Traceable Grounding & Audit Trail Inspection (Priority: P3)

**Goal**: Full operational traceability and audit inspection: fetch committed order details (`GET /api/v1/orders/{order_id}`), display field-level source grounding citations with exact canonical text offsets for headers and lines, and render an interactive chronological audit trail of all lifecycle events (ingestion, AI extraction, operator corrections, SKU selections, line removals, rejection, approval). Tasks T035, T036, and T037 can start as soon as US1 VerifiedOrderRecord/audit foundation is complete and can execute in parallel with US2; T038 is serialized after T034 to prevent concurrent edits to `app/static/js/app.js`.

**Independent Test**: Load an approved order via `GET /api/v1/orders/{order_id}` and confirm that all mandatory header fields and line items include verbatim textual snippets matching `PurchaseOrderDocument.raw_text`, exact canonical character offsets, contract price comparisons, and full chronological `AuditEvent` records. In the web UI, open the provenance drawer and audit trail viewer to verify grounded evidence presentation.

### Tests for User Story 3 ⚠️

- [x] T035 [P] [US3] Integration tests for order retrieval and audit inspection in `tests/integration/test_api_contracts.py` validating `GET /api/v1/orders/{order_id}` returns committed order attributes, header provenance, line item provenance with verbatim snippets and character offsets, and chronological `audit_trail` events

### Implementation for User Story 3

- [x] T036 [US3] Implement order retrieval endpoint in `app/api/routes_orders.py` (`GET /api/v1/orders/{order_id}`) returning `VerifiedOrderRecord`, associated `FieldProvenance` records, line items snapshot, and chronological `AuditEvent` list
- [x] T037 [P] [US3] Implement field provenance drawer component in `app/static/js/components/provenance_drawer.js` displaying verbatim extracted text snippets, document line/page numbers, character offsets, and matching confidence when clicking on any header or line field
- [x] T038 [US3] Extend SPA UI in `app/static/js/app.js` and `app/static/index.html` (serialized after T034 completion to prevent concurrent modifications to `app.js`) to include an audit trail viewer displaying chronological lifecycle events (`DocumentIngested`, `AIExtractionCompleted`, `SKUSelected`, `FieldCorrected`, `LineRemoved`, `DraftRejected`, `OrderApproved`) with actor details and timestamps

**Checkpoint**: All user stories (P1, P2, P3) are now independently functional, testable, and demonstrable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: End-to-end verification, demo verification script, developer documentation, and quality assurance across all user stories

- [x] T039 [P] Implement automated verification suite in `tests/integration/test_quickstart_scenarios.py` validating all 5 scenarios from `specs/001-ordershield-po-reconciliation/quickstart.md` using controlled mocks for provider timing/failure semantics (Scenario 1 Happy Path, Scenario 2 Discrepancy & Ambiguity Resolution, Scenario 3 Provider Error Handling with controlled mocks verifying ≤5s immediate failure and ≤15s stalled timeout with zero failover / zero fixture substitution / zero partial persistence, Scenario 4 Replay Mode Banner, Scenario 5 Audit Trail & Provenance)
- [x] T040 [P] Create comprehensive repository documentation in `README.md` covering prerequisites (Python 3.11+), environment configuration (`LLM_PROVIDER`, `LLM_API_KEY`), database initialization and seeding command (`python -m app.cli init-db --seed`), single-command startup (`python -m uvicorn app.main:app`), offline demo execution, and test commands (`pytest -v`)
- [x] T041 Execute repository verification checks using standard Python compilation (`python -m compileall app tests`), pytest execution, and git status hygiene verification in `app/` and `tests/` without introducing external linter/formatter dependencies outside the accepted dependency set

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup (Phase 1) completion - BLOCKS all user stories
- **User Story 1 (Phase 3 - P1 MVP)**: Depends on Foundational (Phase 2) completion - establishes the core end-to-end intake and approval slice
- **User Story 2 (Phase 4 - P2)**: Depends on User Story 1 (Phase 3) core pipeline - adds discrepancy detection, catalog search, grounded corrections, and rejection
- **User Story 3 (Phase 5 - P3)**:
  - T035, T036, and T037 depend only on the User Story 1 (Phase 3) `VerifiedOrderRecord` and `AuditEvent` foundation and may execute in parallel with User Story 2 (Phase 4).
  - T038 depends on T034 completion to serialize changes to `app/static/js/app.js`.
- **Polish (Phase 6)**: Depends on completion of all required P1, P2, and P3 user stories

### User Story Dependencies

- **User Story 1 (P1 MVP)**:
  - Can start immediately once Foundational (Phase 2) is complete.
  - No dependencies on User Story 2 or User Story 3.
  - Delivers the complete independently testable MVP.
- **User Story 2 (P2)**:
  - Can start after User Story 1 core intake and draft models are functional.
  - Extends reconciliation engine (`app/services/reconciliation.py`) and drafts routes (`app/api/routes_drafts.py`).
  - Does not block User Story 1 core clean path.
- **User Story 3 (P3)**:
  - T035, T036, and T037 can start as soon as User Story 1 verified order record and audit events are defined, proceeding in parallel with User Story 2.
  - T038 waits for T034 to avoid concurrent edits to `app/static/js/app.js`.
  - Independently testable against approved order records.

### Within Each User Story

- Tests MUST be written FIRST and fail before implementation
- Models and schemas before services
- Services before API endpoints
- API endpoints before UI components
- Story complete and verified before declaring phase done

### Parallel Opportunities

- **Setup Phase**: T002, T003, T004 can proceed concurrently once T001 is initialized.
- **Foundational Phase**:
  - T006 and T007 both modify `app/models/entities.py` and are serialized (T006 catalog/contracts, then T007 reconciliation domain models).
  - T008 (Pydantic schemas), T009 (Document parser), T011 (CLI seeding), and T012 (Fixtures) can proceed in parallel once T005 is established.
- **User Story 1 Phase**:
  - T013, T014, T015, T016 (Tests) can all be written concurrently.
  - T020 (Orders & Fixtures routes), T021 (Drafts routes), T023 (HTML workspace), T024 (CSS styling) can proceed in parallel once T017, T018, T019 service core is structured.
- **User Story 2 Phase**:
  - T026, T027 (Tests) can be written concurrently.
  - T030 (Catalog route), T031 (Drafts patch/delete/reject routes), and T033 (Catalog modal UI) can proceed concurrently once T028 and T029 are complete.
- **User Story 3 Phase**:
  - T035 (Integration test), T036 (Order retrieval route), and T037 (Provenance drawer UI) can execute concurrently and in parallel with User Story 2 once User Story 1 is complete.
  - T038 is serialized after T034 on `app/static/js/app.js`.
- **Polish Phase**:
  - T039 (Quickstart scenario test suite) and T040 (README documentation) can run concurrently.

---

## Parallel Example: User Story 1

```bash
# Launch test creation for User Story 1 concurrently:
Task T013: "Unit tests for document parser in tests/unit/test_document_parser.py"
Task T014: "Unit tests for deterministic contract tier pricing in tests/unit/test_pricing_tiers.py"
Task T015: "Unit tests for OrderShieldAIProvider boundary in tests/unit/test_ai_provider.py"
Task T016: "Integration tests for clean PO intake in tests/integration/test_api_contracts.py"

# Launch decoupled UI styling and API routes concurrently:
Task T020: "Implement live document intake and fixture replay routes in app/api/routes_orders.py and app/api/routes_fixtures.py"
Task T021: "Implement draft inspection and atomic approval routes in app/api/routes_drafts.py"
Task T023: "Implement offline HTML workspace structure in app/static/index.html"
Task T024: "Implement committed local CSS styling in app/static/css/styles.css"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001 - T004)
2. Complete Phase 2: Foundational Prerequisites (T005 - T012)
3. Complete Phase 3: User Story 1 (T013 - T025)
4. **STOP and VALIDATE**: Run `pytest tests/unit/ tests/integration/test_api_contracts.py -v` and verify clean intake Scenario 1 from `quickstart.md`
5. Demonstrate working P1 vertical slice (clean intake, extraction, pricing tier selection, Ready for Approval, atomic approval, VerifiedOrderRecord persistence, replay isolation)

### Incremental Delivery

1. Complete Setup + Foundational → Database, ORM, decimal pricing, and document parser operational
2. Add User Story 1 (P1) → Test independently → Deliver MVP!
3. Add User Story 2 (P2) & early User Story 3 (T035–T037) in parallel → Test independently
4. Complete User Story 2 UI (T034) then User Story 3 UI (T038) → Deliver full audit trail inspection and field provenance drawer
5. Complete Phase 6 Polish → Run full quickstart suite (`test_quickstart_scenarios.py`) and repository verification

### Parallel Team Strategy

With multiple developers:
1. Team establishes Setup + Foundational together (T001–T012, serializing T006 and T007)
2. Once Foundational is complete:
   - Developer A (Fast Executor): Core service pipeline & API routes (T017, T018, T019, T020, T021)
   - Developer B: Offline UI workspace & styling (T023, T024, T025)
   - Developer C: Test suites & failure semantics verification (T013, T014, T015, T016)
3. P1 MVP is validated and merged.
4. For P2 & P3:
   - Developer A implements P2 reconciliation rules and routes (T028, T029, T030, T031)
   - Developer B implements P3 order retrieval route & provenance drawer (T036, T037)
   - Developer C implements P2 tests and P3 tests (T026, T027, T035)
   - Developer B then implements P2 UI (T034), followed by P3 audit UI (T038)

---

## Notes

- `[P]` tasks = different files, no dependencies on unfinished tasks
- `[Story]` label maps task directly to user stories (`[US1]`, `[US2]`, `[US3]`) for full traceability
- Exact integer cents in SQLite storage (`INTEGER`), mapped to `decimal.Decimal` with 2 decimal places at domain/API boundaries
- All business rules (pricing tiers, MOQ/packaging, discrepancies, status transitions, approval) are 100% deterministic (zero AI delegation)
- AI provider boundary enforces primary Alibaba Qwen 3.8 Flash (reasoning disabled) and fallback candidate Google Gemini 3.5 Flash-Lite (minimal thinking) with bounded ≤15s live inference budget and target ≤5s immediate error handling per ADR 0001
- Zero automatic runtime provider failover and zero silent fixture fallback
- Replay mode is entered strictly via dedicated `/api/v1/fixtures/...` routes with committed pre-verified payloads in `app/fixtures/`, visibly badged across 100% of views; no configuration flag may redirect live `/api/v1/orders/ingest` traffic
- Automated timing/failure testing uses controlled mocks/fakes; real-provider timing verification is conducted via manual live testing
- Provenance for live provider/model and replay fixture datasets is recorded in the accepted `AuditEvent.details_json` field without unapproved schema modifications
- No external linters or formatters outside the accepted project dependencies are introduced
- Every task includes an explicit file path and verifiable acceptance criteria
