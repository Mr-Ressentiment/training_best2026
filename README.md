# OrderShield

OrderShield is an intelligent purchase-order reconciliation system designed to automate wholesale document intake, eliminate commercial order discrepancies, and maintain strict human-in-the-loop governance.

Customer purchase orders (.txt and .pdf) are ingested, extracted, and deterministically validated against master catalog rules and negotiated customer contract pricing tiers. Discrepancies are flagged for human review, and only clean, operator-resolved orders can transition to immutable approved records with comprehensive audit trails and document-grounded provenance.

---

## What it does

OrderShield manages the end-to-end purchase order reconciliation lifecycle:

```text
Purchase Order (.txt / .pdf)
    ↓
AI Extraction Boundary (fuzzy parsing & semantic SKU candidates)
    ↓
Deterministic Reconciliation Engine (catalog & contract pricing rules)
    ↓
Discrepancy Review & Grounded Source Evidence
    ↓
Human Operator Resolution (SKU selection, grounded field correction, line removal)
    ↓
Approval or Explicit Rejection
    ↓
Immutable Verified Order Record & Append-Only Audit Trail
```

### Core Responsibilities
- **AI Boundary**: Generates initial field extraction and semantic candidate SKU matching. AI is strictly untrusted for business calculations and approval decisions.
- **Deterministic Validation**: Executes all catalog checks, packaging increment math, integer-cents pricing tier lookups, arithmetic checks, and draft state transitions.
- **Zero Autonomous Approval**: AI models cannot approve orders. Approval strictly requires human operator action on drafts with zero unresolved blocking discrepancies (`Ready for Approval`).

### Supported Discrepancy Types
The reconciliation engine evaluates and flags four accepted MVP discrepancy categories:

1. **`PriceMismatch`**: The requested purchase order unit price does not match the customer's negotiated contract price tier for the given quantity.
2. **`QuantityOrPackagingBreach`**: The requested quantity violates the master catalog minimum order quantity (MOQ) or packaging increment rules.
3. **`ArithmeticMismatch`**: The line total does not equal `quantity * unit_price`, or the order total does not equal the sum of line totals.
4. **`CatalogMatchingMismatch`**: The extracted customer item description does not map to a single catalog SKU with high confidence (e.g., ambiguous candidate matches or unrecognized items).

---

## Architecture & Trust Model

### Architecture Overview

```text
Browser SPA (Vanilla JS / CSS / HTML)
    ↓
FastAPI Application Layer
    ├── Live AI Extraction Boundary (Qwen / Gemini)
    ├── Replay Fixture Boundary (Committed Pre-Verified Datasets)
    ├── Deterministic Reconciliation Engine (Integer Cents Math)
    ├── Operator Mutation APIs (SelectSKU, CorrectField, RemoveLine)
    ├── Order Lifecycle Management (Approve / Reject)
    └── SQLite Persistence (ORM Models, Field Provenance, Audit Events)
```

### Trust & Governance Model

- **NO Automatic Provider Failover**: The application exclusively invokes the configured primary AI provider. If the provider experiences an error, network failure, or timeout, the request fails fast and explicitly. The system never dynamically cascades to a secondary provider.
- **NO Silent Fixture Substitution**: Live intake (`/api/v1/orders/ingest`) never falls back to mock or replay fixture data upon live inference failure. Live failures return explicit HTTP diagnostics.
- **NO Commercial Overrides**: Human operators cannot arbitrarily override prices or quantities to bypass contractual agreements or catalog rules. Line corrections (`CorrectField`) require verbatim text snippets grounded in the source document.
- **NO Unresolved Approvals**: Drafts containing any unresolved blocking discrepancies cannot be approved (`DraftNotReadyForApprovalError`, HTTP 409).
- **Explicit Replay Segregation**: Offline / replay demo mode is initiated exclusively through dedicated endpoints (`/api/v1/fixtures/...`) and is permanently badged in the UI as `⚠️ DEMO / REPLAY MODE (NON-LIVE FIXTURE DATA)` with no dismiss control.
- **Auditable Provenance**: Audit events record the actual provider and model identity (e.g. `qwen / qwen3.8-flash` or `fixture / pre-verified-dataset`).

---

## Prerequisites

- **Python**: Version `3.11` or higher
- **Package Manager**: `pip`
- **Version Control**: `Git`
- **Operating System**: Windows, Linux, or macOS

> [!NOTE]
> Docker is not required. Node.js/npm is also not required; the web interface is committed static HTML, CSS, and vanilla JavaScript without any frontend build or bundling step.

---

## Installation

Clone the repository and set up a Python virtual environment:

```bash
git clone https://github.com/Slave-of-Skynet/training_best2026.git
cd training_best2026

# Create virtual environment
python -m venv .venv
```

Activate the virtual environment:

### Windows PowerShell
```powershell
.\.venv\Scripts\Activate.ps1
```

### Linux / macOS
```bash
source .venv/bin/activate
```

Install application dependencies:

```bash
pip install -r requirements.txt
```

---

## Configuration

Runtime configuration is read directly from environment variables via Python `os`.

> [!IMPORTANT]
> The application does **not** automatically load `.env` files at startup. The file `.env.example` serves as a configuration reference. Set environment variables explicitly in your shell before launching the server.

### Environment Reference (`.env.example`)

```text
LLM_PROVIDER="qwen"
LLM_API_KEY=""
DATABASE_URL="sqlite:///ordershield.db"
LIVE_INFERENCE_TIMEOUT=15.0
IMMEDIATE_FAILURE_TIMEOUT=5.0
```

### Configuration Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `LLM_PROVIDER` | `"qwen"` | Live AI extraction provider. Supported options: `"qwen"` (primary: Alibaba Qwen 3.8 Flash) or `"gemini"` (Google Gemini 3.5 Flash-Lite). |
| `LLM_API_KEY` | `""` | API authentication key for the configured provider. Required for live intake. |
| `DATABASE_URL` | `"sqlite:///ordershield.db"` | SQLAlchemy database connection URL. |
| `LIVE_INFERENCE_TIMEOUT` | `15.0` | Maximum client timeout in seconds for live AI inference before aborting. |
| `IMMEDIATE_FAILURE_TIMEOUT`| `5.0` | Reference/configuration value for the immediate-failure target used by the accepted requirements/tests; LiveAIProvider does not currently enforce it as a separate timer. |

### Setting Environment Variables

#### Windows PowerShell
```powershell
$env:LLM_PROVIDER="qwen"
$env:LLM_API_KEY="your-api-key-here"
```

#### Linux / macOS
```bash
export LLM_PROVIDER="qwen"
export LLM_API_KEY="your-api-key-here"
```

*Provider Semantics*: Switching providers requires setting `LLM_PROVIDER` and restarting the application. There is zero runtime failover.

---

## Initialize the Database

Initialize the local SQLite database schema and seed deterministic baseline catalog products, customer contracts, and pricing tiers:

```bash
python -m app.cli init-db --seed
```

This command creates the default SQLite database file `ordershield.db` and populates:
- 12 baseline catalog products (packaging, shipping, warehouse, safety);
- 2 baseline customer contracts (`CONTRACT-ACME-2026`, `CONTRACT-APEX-2026`);
- 11 quantity-tiered contract pricing rules.

### Local SQLite Schema Reset

OrderShield uses SQLAlchemy `Base.metadata.create_all()`, which creates missing tables but does not apply schema migrations to existing tables. When pull requests modify ORM schemas, recreate the disposable local database:

#### Windows PowerShell
```powershell
Remove-Item .\ordershield.db -ErrorAction SilentlyContinue
python -m app.cli init-db --seed
```

#### Linux / macOS
```bash
rm -f ./ordershield.db
python -m app.cli init-db --seed
```

> [!WARNING]
> Do not execute this reset on a database containing data that must be preserved. Production database migration frameworks (e.g. Alembic) are outside the scope of this MVP.

---

## Run OrderShield

Start the FastAPI application with Uvicorn:

```bash
python -m uvicorn app.main:app
```

For local development with automatic reload on code changes:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### Accessing the Web Application
Open your web browser to the committed Single Page Application (SPA) entrypoint:

```text
http://127.0.0.1:8000/static/index.html
```

> [!NOTE]
> Static assets are mounted under `/static`. FastAPI does not serve an HTML route at root `/`, nor does it define HTML routes for individual orders.

---

### Live Mode

Live mode requires a valid API key for the configured provider (`qwen` or `gemini`).

1. Configure environment variables (`LLM_PROVIDER`, `LLM_API_KEY`).
2. Initialize and seed the database (`python -m app.cli init-db --seed`).
3. Start the server (`python -m uvicorn app.main:app`).
4. Navigate to `http://127.0.0.1:8000/static/index.html`.
5. Under **Live document intake**, upload a customer PO file (`.txt` or `.pdf`, such as `tests/fixtures/po_clean_acme.txt`).
6. The file is uploaded to `POST /api/v1/orders/ingest`, parsed, extracted via live AI, and deterministically reconciled.

If provider credentials are missing, invalid, or the provider network times out, intake fails with an explicit HTTP 503 diagnostic error. Live mode will **never** silently fall back to replay fixtures.

---

### Offline / Replay Demo

For reliable demonstrations, evaluation, and environments without active AI provider credentials or external internet access, OrderShield provides an explicit replay mode.

- Uses committed pre-verified extraction datasets.
- Segregated from live intake via dedicated endpoints (`/api/v1/fixtures/...`).
- Visually disclosed with an unclosable banner: `⚠️ DEMO / REPLAY MODE (NON-LIVE FIXTURE DATA)`.
- Never touches live provider credentials or external networks.

#### Running Replay Mode
1. Start the server:
   ```bash
   python -m uvicorn app.main:app
   ```
2. Open `http://127.0.0.1:8000/static/index.html`.
3. In the **Replay & demo datasets** panel, select one of the registered fixtures.

Alternatively, trigger replay ingestion via `curl`:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/fixtures/fixture-clean-acme/ingest"
```

#### Committed Replay Fixtures

- **`fixture-clean-acme`** (`clean_acme.json`):
  Happy path for Acme Industrial Supplies (`CUST-ACME`). Both line items match catalog SKUs with high confidence and contracted pricing tiers. Status: `Ready for Approval` (0 discrepancies).
- **`fixture-discrepancy-apex`** (`discrepancy_apex.json`):
  Discrepancy scenario for Apex Distribution (`CUST-APEX`). Line 1 has a `PriceMismatch` ($18.00 requested vs $22.00 contract). Line 2 has an ambiguous SKU (`CatalogMatchingMismatch`). When the operator selects `SKU-WRAP-15`, deterministic revalidation reveals a genuine `QuantityOrPackagingBreach` (customer requested quantity 2, below catalog MOQ 5). Demonstrates blocked approval and explicit operator rejection.
- **`fixture-ambiguous-apex`** (`ambiguous_apex.json`):
  Compliant scenario requiring operator ambiguity resolution. Line item contains description ambiguity; operator selects `SKU-WRAP-15`, which satisfies pricing and MOQ rules, transitioning draft to `Ready for Approval`.

---

## Demo Workflow

A suggested 5-minute walkthrough to demonstrate key system capabilities:

1. **Clean Replay Ingestion**:
   - In the Replay panel, select `fixture-clean-acme` and click **Ingest selected fixture (replay)**.
   - Note the prominent replay warning banner.
   - Observe deterministic reconciliation: integer-cents pricing tier applied, 0 discrepancies, status is `Ready for Approval`.
2. **Inspect Source Grounding**:
   - Click **Source** next to header fields or line items.
   - Inspect verbatim source text citations and document character offsets.
3. **Approve Clean Order**:
   - Click **Approve Order** (with operator ID `demo-user`).
   - Draft transitions to terminal `Approved` status; order number `VO-2026-0001` is assigned.
   - Review the append-only chronological audit log showing ingestion, extraction, and approval.
4. **Discrepancy & Ambiguity Handling**:
   - Load `fixture-discrepancy-apex`.
   - Observe status is `Needs Review` and the **Approve Order** button is disabled.
   - Review Line 1 `PriceMismatch`: Delete the non-compliant commercial line (`Remove Line`).
   - Review Line 2 `CatalogMatchingMismatch`: Click **Select SKU** and choose candidate `SKU-WRAP-15`.
   - Observe immediate deterministic revalidation: revealing a genuine `QuantityOrPackagingBreach` (requested 2 < MOQ 5).
5. **Enforce Governance & Rejection**:
   - Observe that **Approve Order** remains strictly disabled because a genuine commercial violation cannot be bypassed without altering source order facts.
   - Click **Reject Order**, provide reason *"Valid MOQ breach cannot be resolved from source order"*.
   - Draft transitions to terminal `Rejected` status with a `DraftRejected` audit event logged.

---

## Testing & Verification

### Running the Test Suite
Execute the full test suite using `pytest`:

```bash
pytest -v
```

> Current test baseline: **736 passing tests**.

### End-to-End Quickstart Integration Scenarios
Run the dedicated scenario verification suite covering happy path, discrepancy resolution, provider failure semantics, replay isolation, and audit trail inspection:

```bash
pytest tests/integration/test_quickstart_scenarios.py -v
```

### Python Bytecode Compilation Check
Verify all Python source files compile cleanly:

```bash
python -m compileall app tests
```

---

## Project Structure

```text
app/
  api/          FastAPI route handlers and response serialization
  fixtures/     Committed pre-verified replay datasets (.json)
  models/       SQLAlchemy domain entities and Pydantic validation schemas
  services/     AI provider boundary, document parser, reconciliation engine, order service
  static/       Committed single-page web UI (HTML, CSS, JavaScript)

tests/
  fixtures/     Sample PO documents (.txt, .pdf)
  integration/  API contract tests, lifecycle tests, and quickstart scenarios
  unit/         Reconciliation engine, pricing tiers, and parser unit tests

specs/001-ordershield-po-reconciliation/
  spec.md       Formal feature specification and requirements
  plan.md       Architectural design and execution plan
  tasks.md      Dependency-ordered task tracker
  quickstart.md End-to-end verification and scenario guide
  contracts/    API endpoint and schema specifications
```

---

## Important API Routes

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/orders/ingest` | Live document intake (.txt / .pdf multipart upload) |
| `GET` | `/api/v1/fixtures` | List available committed replay datasets |
| `POST` | `/api/v1/fixtures/{fixture_id}/ingest` | Ingest a pre-verified replay fixture |
| `GET` | `/api/v1/drafts/{draft_id}` | Retrieve draft details, lines, discrepancies, and provenance |
| `PATCH` | `/api/v1/drafts/{draft_id}/lines/{line_id}` | Operator line mutation (`SelectSKU` or `CorrectField`) |
| `DELETE`| `/api/v1/drafts/{draft_id}/lines/{line_id}` | Operator line removal (`RemoveLine`) |
| `POST` | `/api/v1/drafts/{draft_id}/approve` | Approve order draft (requires `Ready for Approval`) |
| `POST` | `/api/v1/drafts/{draft_id}/reject` | Explicitly reject order draft with mandatory reason |
| `GET` | `/api/v1/orders/{order_id}` | Retrieve approved verified order record, audit trail, and snapshot |
| `GET` | `/api/v1/catalog` | Master catalog search by keyword and category |

> [!NOTE]
> In `GET /api/v1/orders/{order_id}`, `{order_id}` represents the internal database record ID (UUID) returned by the approval API response, distinct from the human-readable order number (e.g. `VO-2026-0001`).

---

## Important MVP Limitations

1. **Local SQLite Storage**: The MVP uses local SQLite with `Base.metadata.create_all()`. There is no incremental migration framework (such as Alembic); schema changes require deleting `ordershield.db` and re-running `init-db --seed`.
2. **Single-Process Architecture**: Built for local evaluation and hackathon demonstration. Distributed background worker queues, horizontal scaling, and clustered databases are out of MVP scope.
3. **No Automatic AI Failover**: Designed as a governance safeguard. AI provider errors return explicit HTTP 503 diagnostics rather than masking failures with fallback providers or silent mock data.
4. **Rejected-Draft Audit Inspection**: While `DraftRejected` audit events are persisted in the database upon order rejection, the public retrieval endpoint `GET /api/v1/orders/{order_id}` is scoped specifically to approved verified orders (`HG-P3-AUDIT-01`). There is currently no dedicated REST endpoint for inspecting rejected draft audit trails.
5. **Static SPA Routing**: The user interface is mounted at `/static/index.html`. No root `/` redirect or server-rendered order detail routes exist.
6. **Authentication & Authorization**: Operator identity is supplied as an identifier string (e.g. `operator_id`); enterprise authentication, OAuth/SAML, and RBAC are outside current MVP scope.
