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

### 1.4 AI Provider Boundary, Untrusted Output & Reconciled Runtime Timing

- **Decision**: Pluggable, provider-neutral `OrderShieldAIProvider` protocol with concrete model selection for the training/demo live path (formalized in [ADR 0001](../../docs/decisions/0001-training-live-ai-provider-selection.md)), strict boundary isolation, and reconciled runtime timeout:
  1. **Primary Training Live Provider**:
     - **Model**: Alibaba Qwen 3.8 Flash (`qwen3.8-flash`) via DashScope / OpenAI-compatible endpoint.
     - **Configuration**: Reasoning / thinking disabled.
     - **Role**: Primary provider for live interactive intake, verification testing, and demonstration.
  2. **Secondary / Fallback Candidate & Fallback Governance**:
     - **Model**: Google Gemini 3.5 Flash-Lite (`gemini-3.5-flash-lite`) via Google GenAI API.
     - **Configuration**: Minimal thinking.
     - **Role**: Validated secondary provider candidate if primary training provider experiences localized disruption.
     - **No Automatic Runtime Provider Failover**: The application runtime does **NOT** attempt dynamic failover between providers upon failure; requests fail explicitly with diagnostic errors.
     - **No Silent Provider Substitution**: The system must never silently substitute one live provider or model for another without explicit direction, just as it must never silently fall back to fixtures.
     - **Explicit Operator Action Required**: Provider switching requires explicit administrative/operator configuration (`LLM_PROVIDER=gemini`) and service restart.
     - **Auditable Provenance**: Extracted drafts, audit events, and provenance records must capture the exact active provider and model identifier (`provider="qwen"`, `model="qwen3.8-flash"` vs `provider="gemini"`, `model="gemini-3.5-flash-lite"`).
  3. **Candidates Not Selected for Training Live Path**:
     - **Gemini 3.8 Flash**: Not selected due to repeated free-tier HTTP 503 / availability evidence during bake-off execution. This is strictly an operational free-tier availability rejection and **NOT** a model-quality or reasoning-capability rejection.
     - **Gemini 3.7 Flash**: Preflight smoke worked with ~13.7 s minimal smoke latency; not advanced into the full bake-off.
  4. **Empirical Finalist Evidence (Combined Phase 1 Baseline + Phase 2 Stability, 24 attempts/finalist)**:
     - *Alibaba Qwen 3.8 Flash*: 24/24 completed; exact mandatory fields 185/192; determinate SKU 12/12; review traps 12/12; grounding/null validity 192/192; 0 wrong-confident SKUs; 0 ambiguous-to-clean promotions.
     - *Google Gemini 3.5 Flash-Lite*: 24/24 completed; exact mandatory fields 187/192; determinate SKU 9/12; review traps 12/12; grounding/null validity 184/192; 0 wrong-confident SKUs; 0 ambiguous-to-clean promotions.
     - *Repeated Fixture Findings*:
       - `h05`/`h06`/`h09` review traps: both routed correctly to human review 4/4.
       - `h07`/`h08` distinguishing SKUs: both selected required SKU 4/4 (`GLOVE-N-L`, `GLOVE-N-M`).
       - `h08` exact description: Gemini 0/4 (included extraneous source document text); Qwen 4/4 exact.
       - `h10` null extraction: both 16/16 values correct.
       - `h10` null provenance: Gemini 8/16 (emitted `"[unreadable]"` quote tokens for null quantities/prices); Qwen 16/16.
       - `h10` SKU: Gemini 1/4 (3 conservative abstentions below confidence threshold); Qwen 4/4 (`PAPER-A4-80`).
     - *Phase-2 Latency Profile*:
       - Gemini 3.5 Flash-Lite: p50 = 6.918 s, observed max = 9.793 s.
       - Qwen 3.8 Flash: p50 = 7.338 s, observed max = 9.414 s.
  5. **Explicit Operational Limitations**:
     - Synthetic frozen corpus (`h01`–`h10`).
     - Repeated stability testing conducted only on selected `h05`–`h10` fixtures.
     - Four observations per selected fixture demonstrate short-term stability but do not establish production reliability.
     - Free-tier availability and quota windows do not establish production suitability or enterprise SLAs.
     - Paid production candidates were researched but not empirically tested in this bake-off.
     - Alibaba Qwen 3.8 Flash is selected strictly as the **TRAINING/DEMO** live provider.
     - Production provider selection remains undecided and deferred to future decision gates.
  6. **Runtime Timeout Reconciliation & Approved SC-006 Semantics (Human Gate)**:
     - Earlier planning assumed a ≤4.5 s client timeout on live inference. Empirical bake-off evidence demonstrates that healthy successful live inference empirically requires a budget of up to ~15 s (p50 ~7.0–7.3 s, max ~9.4–9.8 s), refuting ≤4.5 s as a viable universal inference deadline.
     - **Reconciled Lifecycle & Approved Human Gate Semantics**:
       - *Immediately Detectable Failures*: Unreadable/local document failure, connection refusal, authentication failure, quota/rate-limit rejection, and explicit upstream 4xx/5xx must surface an explicit diagnostic error with a target of ≤5 seconds from request intake.
       - *Healthy Live Inference*: Operates within a bounded ≤15-second live inference budget consistent with observed empirical latency.
       - *Silent / Stalled Provider Inference*: May remain indistinguishable from healthy slow inference until the bounded client deadline; must be aborted at ≤15 seconds and return an explicit diagnostic error, with no partial persistence and no silent fallback.
       - *Zero Automatic Failover*: No automatic provider failover or fixture substitution is permitted. (See [ADR 0001](../../docs/decisions/0001-training-live-ai-provider-selection.md)).
  7. **Untrusted Output Policy & Boundary Safety**:
     - Model responses are treated as **untrusted external input**. The system validates raw provider output against an application-owned Pydantic schema before passing data to the reconciliation engine. If parsing or schema validation fails, the system fails explicitly and fabricates zero drafts.
     - **No Silent Fallback**: Live intake MUST NEVER fall back to fixture data upon failure.
  8. `FixtureAIProvider`: Loads pre-verified synthetic extraction/matching datasets from disk exclusively when the user invokes the dedicated fixture endpoint (`POST /api/v1/fixtures/{fixture_id}/ingest`). Every fixture-generated draft is explicitly flagged with `is_replay_mode=true` and visibly badged across all UI views.
- **Rationale**:
  - Reconciles empirical provider bake-off evidence (`spikes/ordershield/provider_bakeoff/`) with operational demo needs.
  - Complete decoupling of live user uploads from pre-recorded fixture runs.
  - Eliminates false-positive client timeouts during live demonstration while maintaining strict SC-006 failure diagnostics.

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

The six architectural decisions are **APPROVED** (with Gate 4 reconciled and accepted via [ADR 0001](../../docs/decisions/0001-training-live-ai-provider-selection.md)):

| Gate | Approved In Principle | Final Reconciled Design | Status |
|:---|:---:|:---|:---:|
| **1. Application Architecture** | Yes | Single-service modular monolith (Python 3.11 + FastAPI). | **Approved** |
| **2. Client/Server Boundary** | Yes | Decoupled RESTful API (`/api/v1`) with separate live intake (`POST /orders/ingest`) and fixture replay (`POST /fixtures/{id}/ingest`); catalog search endpoint (`GET /catalog?query=...`); local static SPA frontend. | **Approved** |
| **3. Persistent Data Model** | Yes | Relational SQLite schema with exact integer cents persistence, `ContractPriceTier` pricing, `FieldProvenance` grounding for all mandatory fields, explicit `sku_resolution_source`, line removal discrepancy preservation, terminal state protection, and atomic approval transactions. | **Approved** |
| **4. AI Provider & Boundary** | Yes | Primary live training provider: Alibaba Qwen 3.8 Flash (reasoning disabled); fallback candidate: Google Gemini 3.5 Flash-Lite (minimal thinking) under explicit configuration only (no automatic runtime failover or silent substitution; provenance tracking enforced); pluggable `OrderShieldAIProvider` with bounded ≤15s inference budget and target ≤5s diagnostic failure handling from request intake for immediately detectable errors. Stalled inference is aborted at ≤15s with explicit diagnostic error per approved SC-006 amendment. (See ADR 0001). | **Approved** |
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
