# Implementation Plan: OrderShield Purchase Order Reconciliation

**Branch**: `001-ordershield-po-reconciliation` | **Date**: 2026-09-29 | **Spec**: [specs/001-ordershield-po-reconciliation/spec.md](spec.md) | **Status**: Implemented & Verified (T041 GO)

**Input**: Feature specification from `/specs/001-ordershield-po-reconciliation/spec.md`

---

## Summary

OrderShield transforms unstructured incoming customer purchase orders (`.txt` and text-extractable `.pdf`) into verified, contract-compliant order drafts. The technical implementation employs a strict separation between AI-driven interpretation and deterministic business rules:
- **AI Component**: Structured field extraction, semantic catalog SKU matching, and ambiguity detection via a pluggable, provider-neutral interface (`OrderShieldAIProvider`). Raw AI outputs are strictly treated as untrusted input and validated against application-owned Pydantic schemas.
- **Deterministic Component**: Mathematical calculations, contract pricing tier selection (`ContractPriceTier`), minimum order quantity (MOQ) and packaging increment validation, discrepancy detection (strictly bounded to 4 MVP types), state machine transitions, and persistent storage. All monetary calculations utilize exact fixed-point decimal arithmetic (`decimal.Decimal`) mapped to integer cents in SQLite.
- **Architectural Delivery**: A lightweight, single-service modular monolith using Python 3.11+ and FastAPI serving a modern, responsive HTML5/ES6 Single Page Application using exclusively committed local static assets (zero CDN dependencies). Persistence is managed via embedded SQLite with integer cents storage, and demo reliability is fortified through an explicitly badged, user-initiated non-live fixture/replay path (`/api/v1/fixtures/...`) that never acts as a stealth fallback for failed live inference.
- **Data & Contract Precision**:
  - Exact decimal money mapped to integer cents in SQLite columns.
  - Relational `ContractPriceTier` entity with deterministic quantity-based tier selection.
  - Field-level `FieldProvenance` for all mandatory header and line items.
  - Explicit `sku_resolution_source` (`AI_HIGH_CONFIDENCE` vs `OPERATOR_SELECTED`) evaluated directly for `Ready for Approval`.
  - Manual catalog lookup endpoint (`GET /api/v1/catalog?query=...`) supporting selection of any valid SKU.
  - Line removals preserve full discrepancy history (`ResolvedByLineRemoval`).
  - Server-side grounding verification for field corrections against canonical `raw_text`.

---

## Technical Context

**Language/Version**: Python 3.11+  
**Primary Dependencies**: FastAPI (web service & OpenAPI), Uvicorn (ASGI server), Pydantic v2 (data contracts & validation), PyPDF (pure-Python digital PDF extraction), SQLAlchemy (relational ORM), httpx (HTTP client).  
**Storage**: Embedded SQLite (`ordershield.db` for runtime persistence; `:memory:` for automated tests) storing monetary values as exact **integer cents** (`INTEGER` column types), mapped to Python `decimal.Decimal` with 2 decimal places at domain/API boundaries. Binary floating-point is strictly prohibited.  
**Testing**: `pytest` (unit tests for deterministic decimal reconciliation engine, pricing tiers, and parser; integration tests for API endpoints, terminal states, grounding validation, and failure modes).  
**Target Platform**: Cross-platform (Windows, macOS, Linux).  
**Project Type**: Single-service Web Application (FastAPI backend + static responsive SPA frontend with committed local assets).  
**Performance Goals**: End-to-end reconciliation of prepared 5-line purchase order in <60 seconds during live demo; API response time <500ms for deterministic re-validation; bounded demo-oriented live AI inference target approximately ≤15 seconds; target ≤5 seconds from request intake for immediately detectable failures (SC-006).
**Constraints**: 
- Strictly digital text (`.txt`) and digital PDF with selectable text streams (`.pdf`); zero OCR or scanned image parsing.
- Commercial price/quantity overrides are strictly prohibited.
- Live AI inference uses Alibaba Qwen 3.8 Flash (reasoning disabled) as primary training live provider, with Google Gemini 3.5 Flash-Lite (minimal thinking) as fallback candidate (ADR 0001). Switching providers requires explicit configuration (zero automatic runtime failover or silent substitution); provenance must capture actual provider/model. Live inference execution budget is bounded to ≤15s (reconciling empirical bake-off latency: p50 ~7.3s, max ~9.4s); immediately detectable intake/provider errors have a target of ≤5s from request intake, while silent/stalled inference is aborted at ≤15s with explicit diagnostic error per approved SC-006 amendment; live inference never silently falls back to fixtures.
- Replay/fixture mode must be initiated through dedicated endpoints and 100% visibly badged as non-live across all views.
- Field corrections must be verified server-side against canonical `raw_text`.  
**Scale/Scope**: MVP vertical slice supporting single-order intake against a pre-loaded catalog of 10–30 SKUs and customer contract terms.

---

## Constitution Check

*GATE: Evaluated against `.specify/memory/constitution.md`*

| Principle | Requirement | Plan Status | Compliance Notes |
|:---|:---|:---:|:---|
| **I. Canonical Git Repository** | Repo is single source of truth | **PASS** | All specs, models, contracts, and fixtures committed to feature branch. |
| **II. Spec-Driven Development** | Full spec before implementation | **PASS** | Specification frozen in `spec.md`; plan directly implements accepted requirements. |
| **III. Mandatory Human Decision Gates** | Explicit human sign-off on 6 gates | **APPROVED** | 6 architectural gates approved in principle by Project Brain; data contracts reconciled. |
| **IV. Architectural Decision Records** | Document context, rationale, tradeoffs | **PASS** | Evaluated and recorded in `research.md` and `docs/decisions/0001-training-live-ai-provider-selection.md`. |
| **V. Vertical Slice Simplicity** | Smallest working end-to-end slice | **PASS** | Single-service architecture, embedded SQLite, zero microservices or node build steps. |
| **VI. Test Integrity** | No weakening or skipping tests | **PASS** | Pure deterministic tests for arithmetic/rules; mockable AI provider interface. |
| **VII. Verifiable Acceptance Criteria** | Objective, verifiable criteria | **PASS** | Concrete scenarios defined in `quickstart.md` matching `spec.md`. |
| **VIII. Contract Preservation** | Respect accepted contracts | **PASS** | Strictly bounded to the 4 accepted MVP discrepancy categories. |
| **IX. Security & Least Privilege** | No secrets in repo; validate inputs | **PASS** | API keys via environment variables; strict Pydantic payload and file type validation. |
| **X. Incremental Delivery** | Partition into reviewable steps | **PASS** | Layered modular architecture: parser → AI boundary → engine → API → UI. |
| **XI. AI Engineering Boundaries** | No silent requirements redefinition | **PASS** | Strict adherence to frozen spec. |
| **XII. Hackathon Optimization** | Rock-solid demo reliability | **PASS** | Single-command launch, prepared test fixtures, and explicit replay mode. |

---

## Project Structure

### Documentation (this feature)
```text
specs/001-ordershield-po-reconciliation/
├── spec.md                  # Frozen feature specification
├── plan.md                  # This implementation plan
├── research.md              # Phase 0: Technical choices, alternatives, and human gates
├── data-model.md            # Phase 1: Entity definitions, schema, decimal money, and state machine
├── quickstart.md            # Phase 1: Setup, verification scenarios, and run commands
├── contracts/
│   └── api-contracts.md     # Phase 1: REST API schemas, endpoints, and UI contract
└── checklists/
    └── requirements.md      # Spec quality validation checklist (16/16 passing)
```

### Source Code (repository root)
```text
app/
├── __init__.py
├── main.py                  # FastAPI app factory, middleware, static mount
├── config.py                # Environment configuration (API keys, demo mode)
├── cli.py                   # CLI commands for DB init and fixture seeding
├── database.py              # SQLite session management and engine
├── api/
│   ├── __init__.py
│   ├── routes_orders.py     # Live document ingestion (POST /orders/ingest) and order retrieval
│   ├── routes_drafts.py     # Draft inspection, line edits, approval, rejection
│   ├── routes_catalog.py    # Master catalog search (GET /catalog?query=...)
│   └── routes_fixtures.py   # Replay intake (POST /fixtures/{id}/ingest) and demo scenarios
├── models/
│   ├── __init__.py
│   ├── entities.py          # SQLAlchemy ORM models (Draft, Line, Provenance, Tier, Discrepancy, Order)
│   └── schemas.py           # Pydantic request/response schemas with Decimal money
├── services/
│   ├── __init__.py
│   ├── document_parser.py   # Pure-Python text extraction (.txt, pypdf) & canonical raw_text
│   ├── ai_provider.py       # Pluggable AI boundary (LiveAIProvider with ≤15s timeout, FixtureAIProvider)
│   ├── reconciliation.py    # Deterministic contract tier pricing, MOQ, and integer-cents math engine
│   └── order_service.py     # High-level coordinator for ingestion and atomic approval
└── static/                  # Responsive Single-Page Application (Committed local assets, 0 CDN)
    ├── index.html           # Unified reconciliation workspace
    ├── css/
    │   └── styles.css       # Committed local styling (100% offline capable)
    └── js/
        ├── app.js           # SPA state management and API communication
        └── components/      # UI components (discrepancy badges, catalog modal, provenance drawer)

tests/
├── conftest.py              # Pytest fixtures and in-memory SQLite setup
├── fixtures/                # Prepared synthetic PO documents and contract data
│   ├── po_clean_acme.txt
│   ├── po_discrepancy_apex.txt
│   ├── po_ambiguous_apex.txt
│   └── po_unextractable.pdf
├── unit/
│   ├── test_document_parser.py
│   ├── test_reconciliation.py
│   ├── test_pricing_tiers.py
│   └── test_ai_provider.py
└── integration/
    └── test_api_contracts.py
```

---

## Complexity Tracking

No constitution violations detected. Simplicity invariants preserved:
- Single runtime process (FastAPI serving static SPA) eliminates Node.js dev server and build pipelines.
- Embedded SQLite with integer cents eliminates external database dependencies and floating-point errors.
- `pypdf` eliminates binary C-library dependencies for digital PDF reading.
- Committed local static CSS eliminates external network CDN dependencies.

---

## Final Human Gate Status

The six high-level architectural decisions are **APPROVED** by Project Brain, with Gate 4 reconciled and finalized following the provider bake-off (ADR 0001):
1. **Application Architecture**: Single-service modular monolith (Python 3.11 + FastAPI). *(Approved)*
2. **Client/Server Boundary**: Decoupled JSON REST API (`/api/v1`) with separate live intake (`POST /orders/ingest`) and fixture replay (`POST /fixtures/{id}/ingest`); catalog search endpoint; local offline-capable HTML5/ES6 SPA frontend. *(Approved & Reconciled)*
3. **Persistent Data Model**: Relational SQLite schema with exact integer cents persistence, `ContractPriceTier` pricing, `FieldProvenance` grounding for all mandatory fields, explicit `sku_resolution_source`, line removal discrepancy preservation, terminal state protection, and atomic approval transactions. *(Approved & Reconciled)*
4. **AI Provider & Boundary**: Pluggable `OrderShieldAIProvider` configured with Alibaba Qwen 3.8 Flash (reasoning disabled) as primary training live provider and Gemini 3.5 Flash-Lite (minimal thinking) as fallback candidate under explicit configuration only (no automatic runtime failover or silent substitution; provenance tracking enforced). Live inference timeout is bounded to ≤15s (reconciling empirical latency), with a target of ≤5s from request intake for immediately detectable errors. Silent/stalled provider inference is aborted at ≤15s with explicit diagnostic error under the approved Human Gate amendment to SC-006; untrusted schema validation and complete prohibition of silent replay fallback remain strictly enforced. *(Approved - ADR 0001)*
5. **Major Dependencies**: Minimal dependency set: `fastapi`, `uvicorn`, `pydantic`, `pypdf`, `sqlalchemy`, `pytest`, `httpx`. Zero OCR or multi-agent libraries. *(Approved)*
6. **Deployment & Runtime**: Zero-config startup with pre-seeded wholesale catalog and customer contract fixtures via `python -m uvicorn app.main:app`; deterministic, offline, reproducible demo paths. *(Approved)*
