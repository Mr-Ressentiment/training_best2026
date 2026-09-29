# Implementation Research: OrderShield Purchase Order Reconciliation

**Feature Branch**: `001-ordershield-po-reconciliation`  
**Date**: 2026-09-29  
**Status**: Completed (Human Gate Final Reconciliation Applied)  

---

## 1. Technical Choices & Evaluation

### 1.1 Application Architecture & Runtime

- **Decision**: Modular Monolithic Single-Service Application using **Python 3.11+** and **FastAPI**.
- **Rationale**:
  - Hackathon delivery requires maximum velocity and zero operational friction. A single-service application eliminates multi-container orchestration, CORS misconfigurations, proxy latency, and dual-process startup dependencies for judges.
  - Python offers native, battle-tested support for document parsing (`pypdf`), schema validation (`pydantic`), SQLite database operations (`sqlite3`/`sqlalchemy`), and AI model integrations.
  - FastAPI provides high-performance asynchronous request handling, automatic OpenAPI/Swagger documentation, and static asset serving in a single runtime process.
- **Alternatives Considered**:
  - *Separate Node.js/React Frontend + Python FastAPI Backend*: Introduces dual development servers, package managers (`npm` + `pip`), proxy configurations, and node build requirements. Rejected to preserve Hackathon Optimization & Simplicity (Constitution Principle V & XII).
  - *Full-stack Next.js/TypeScript*: While proficient for web frontends, server-side document parsing and AI orchestration in Node require more complex libraries and lacks the unified data-science tooling present in Python. Rejected.

---

### 1.2 Frontend Architecture & UI Delivery (100% Offline-Capable)

- **Decision**: Responsive Single-Page Application (HTML5, committed local CSS styling, and modular Vanilla ES6 JavaScript) served statically directly by FastAPI with **zero CDN or external network dependencies**.
- **Rationale**:
  - Enables rich, responsive, and immediate interaction (drag-and-drop document upload, side-by-side document/reconciliation viewer, live discrepancy badges, catalog search modal, one-click approval, and audit trail view).
  - Uses only local, committed static CSS and assets; requires zero runtime internet connectivity to render the UI, eliminating demo failure risks from CDN outages or flaky conference Wi-Fi.
  - Requires zero client build pipeline (`npm run build` or Vite build steps), guaranteeing that any developer or judge cloning the repository can launch the entire UI and backend with a single `uvicorn` command.
- **Alternatives Considered**:
  - *Tailwind CDN Script*: Relies on third-party runtime CDN downloads, violating the demo offline-capability rule. Rejected.
  - *Vite + React SPA*: Requires Node.js installed on the host machine and a separate build step before running. Unnecessary complexity for the focused critical demo path.

---

### 1.3 Document Parsing, Canonical Text & Field-Level Provenance

- **Decision**: Lightweight, pure-Python text extraction using native UTF-8 string decoders for `.txt` and **`pypdf`** for digital `.pdf`. The canonical extracted text string is persisted in `PurchaseOrderDocument.raw_text` as the single source of truth for reproducible grounding.
- **Rationale**:
  - The frozen specification strictly limits MVP intake to plain text (`.txt`) and digital PDFs containing extractable text streams (`.pdf`).
  - `pypdf` is pure-Python, has zero external C-dependencies (no Poppler, libmagic, or Tesseract required), and executes deterministically across Windows, macOS, and Linux.
  - If a PDF lacks extractable text streams (e.g. scanned image), `pypdf` yields empty text, allowing the system to cleanly and immediately raise an explicit `UnextractableTextError` without freezing or attempting OCR.
  - **Field-Level Provenance Model**: Explicit `FieldProvenance` records capture verbatim text snippets and canonical location pointers for all mandatory extracted fields:
    - Headers: `customer_name`, `po_number`
    - Line Items: `customer_description`, `extracted_quantity`, `extracted_unit_price`, `extracted_line_total`
    - Locations: Line/char offset for `.txt`, page + char span for `.pdf`.
  - **Correction Grounding Enforcement**: When an operator corrects a field (`CorrectField`), the server verifies that the corrected value is grounded verbatim in `raw_text` at the specified location, returning HTTP `422 Unprocessable Entity` (`SourceGroundingMismatchError`) on mismatch.
- **Alternatives Considered**:
  - *Punctuation/token matching heuristic (spike artifact)*: Proved brittle during spikes when quotation marks, colons, or whitespace varied. Rejected in favor of explicit canonical span offsets.
  - *OCR libraries (Tesseract, PaddleOCR)*: Explicitly excluded by the frozen specification and Idea Selection Gate.

---

### 1.4 AI Provider Boundary, Untrusted Output & Strict Failure Timing

- **Decision**: Pluggable, provider-neutral `OrderShieldAIProvider` protocol with strict boundary isolation and sub-5-second failure guarantees:
  1. `LiveAIProvider`: Connects to a configurable LLM endpoint (provider-neutral via standard HTTP/JSON).
     - **Strict Timeout**: Configured with a client-level timeout of **≤4.5 seconds** so that any provider timeout, network error, or quota failure is caught, formatted, and returned as an explicit diagnostic error within the **5-second SC-006 bound**.
     - **Untrusted Output Policy**: Model responses are treated as **untrusted external input**. The system validates raw provider output against an application-owned Pydantic schema before passing data to the reconciliation engine. If parsing or schema validation fails, the system fails explicitly and fabricates zero drafts.
     - **No Silent Fallback**: Live intake MUST NEVER fall back to fixture data upon failure.
  2. `FixtureAIProvider`: Loads pre-verified synthetic extraction/matching datasets from disk exclusively when the user invokes the dedicated fixture endpoint (`POST /api/v1/fixtures/{fixture_id}/ingest`). Every fixture-generated draft is explicitly flagged with `is_replay_mode=true` and visibly badged across all UI views.
- **Rationale**:
  - Meets SC-006 performance and failure transparency requirements.
  - Complete decoupling of live user uploads from pre-recorded fixture runs.
  - Provider neutrality avoids early vendor lock-in until a concrete provider/model is formally evaluated and approved through a Human Decision Gate.

---

### 1.5 Deterministic Reconciliation Engine, Tiers & Exact Integer Cents

- **Decision**: Pure-Python Domain Service (`ReconciliationEngine`) enforcing zero AI dependencies, bounded to the 4 supported MVP discrepancy categories, using **exact integer cents in SQLite storage** mapped to `decimal.Decimal` at domain/API boundaries.
- **Rationale**:
  - Binary floating-point (`float`) introduces precision and rounding anomalies that compromise financial reconciliation and automated contract checks.
  - Storing monetary values as exact integer cents (`INTEGER` column type in SQLite) eliminates SQLite floating-point type coercion and guarantees 100% loss-free persistence and deterministic SQL arithmetic.
  - **Contract Pricing Tiers (`ContractPriceTier`)**: Quantity-based pricing is evaluated deterministically by querying tiers where `contract.customer_id == C`, `sku == S`, and `min_quantity <= Q`, picking the tier with the maximum `min_quantity`.
  - **Discrepancy Checks**: Strictly bounded to:
    1. `PriceMismatch`
    2. `QuantityOrPackagingBreach`
    3. `ArithmeticMismatch`
    4. `CatalogMatchingMismatch`
  - **Line Removal Discrepancy Preservation**: Deleting a line sets `status = "Removed"` and transitions associated unresolved discrepancies to `resolution_state = "ResolvedByLineRemoval"`. Discrepancies are never deleted from the database.

---

### 1.6 Persistence Strategy, Database & State Safety

- **Decision**: **SQLite** via standard Python `sqlite3` and `SQLAlchemy`, with strict terminal state enforcement, explicit SKU resolution state, and atomic approval transactions.
- **Rationale**:
  - Zero-configuration, serverless database embedded natively in Python.
  - **State Machine Safety**:
    - `Approved` and `Rejected` are **terminal states**. Any subsequent `PATCH`, `DELETE`, `approve`, or `reject` call on a terminal draft returns `409 Conflict`.
    - `Ready for Approval` requires all active lines to have `matched_sku IS NOT NULL` and `sku_resolution_source IN ('AI_HIGH_CONFIDENCE', 'OPERATOR_SELECTED')` alongside 0 unresolved discrepancies.
    - **Atomic Approval**: State validation, `VerifiedOrderRecord` snapshot creation, and audit event recording execute within a single atomic database transaction.
    - **Idempotency**: Repeated approval calls on an already-approved draft return `409 Conflict` and never create duplicate verified orders.
- **Alternatives Considered**:
  - *PostgreSQL / MySQL*: Requires external running daemon, adding setup friction for judges.

---

## 2. Mandatory Human Decision Gates (Constitution Principle III)

The six architectural decisions are **APPROVED IN PRINCIPLE** with all contract gaps resolved:

| Gate | Approved In Principle | Final Reconciled Design | Status |
|:---|:---:|:---|:---:|
| **1. Application Architecture** | Yes | Single-service modular monolith (Python 3.11 + FastAPI). | **Approved** |
| **2. Client/Server Boundary** | Yes | Decoupled RESTful API (`/api/v1`) with separate live intake (`POST /orders/ingest`) and fixture replay (`POST /fixtures/{id}/ingest`); catalog search endpoint (`GET /catalog?query=...`); local static SPA frontend. | **Approved** |
| **3. Persistent Data Model** | Yes | Relational SQLite schema with exact integer cents persistence, `ContractPriceTier` pricing, `FieldProvenance` grounding for all mandatory fields, explicit `sku_resolution_source`, line removal discrepancy preservation, terminal state protection, and atomic approval transactions. | **Approved** |
| **4. AI Provider & Boundary** | Yes | Pluggable, provider-neutral `OrderShieldAIProvider` with ≤4.5s client timeout (meeting SC-006 ≤5s), untrusted schema validation, and complete prohibition of silent replay fallback. | **Approved** |
| **5. Major Dependencies** | Yes | Minimal footprint: `fastapi`, `uvicorn`, `pydantic`, `pypdf`, `sqlalchemy`, `pytest`, `httpx`. Zero OCR or multi-agent libraries. | **Approved** |
| **6. Runtime & Demo Strategy** | Yes | Single startup command with pre-seeded wholesale catalog and customer contract fixtures; deterministic, offline, reproducible demo paths. | **Approved** |

---

## 3. Technology Inventory & Licensing

| Dependency | Purpose | License | Dependency Risk |
|:---|:---|:---|:---|
| `python >= 3.11` | Core language runtime | PSF | None (Standard) |
| `fastapi` | Web service framework, routing, OpenAPI | MIT | Low (Industry standard) |
| `uvicorn` | ASGI server | BSD-3-Clause | Low |
| `pydantic` | Data parsing, validation, typed contracts | MIT | Low |
| `pypdf` | Pure-Python digital PDF text extraction | BSD-3-Clause | Low (Zero C-dependencies) |
| `sqlalchemy` | ORM & query builder for SQLite | MIT | Low |
| `pytest` | Automated test suite execution | MIT | Low |
| `httpx` | HTTP client for external AI API calls | BSD-3-Clause | Low |
