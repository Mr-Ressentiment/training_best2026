# Quickstart & Verification Guide: OrderShield

**Feature Branch**: `001-ordershield-po-reconciliation`  
**Date**: 2026-09-29  
**Status**: Implemented & Verified (T041 GO)

---

## 1. Prerequisites

- **Python**: Version `3.11` or higher.
- **Operating System**: Windows, macOS, or Linux.
- **Dependencies**: Installed into a Python virtual environment (`.venv`).

---

## 2. Setup & Installation

From the repository root:

```bash
# 1. Create and activate a Python virtual environment
python -m venv .venv

# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate

# 2. Install application dependencies
pip install -r requirements.txt

# 3. Initialize SQLite database and seed baseline catalog/contracts
# For a fresh checkout where no local database exists yet, this command is sufficient:
python -m app.cli init-db --seed
```

### Local SQLite Schema Reset Policy

OrderShield currently uses SQLAlchemy `Base.metadata.create_all()` and does not maintain incremental database migrations for the training/hackathon MVP.

The repository-local `ordershield.db` is disposable runtime state and is excluded from Git.

For a fresh checkout where no local database file exists yet, running:
```bash
python -m app.cli init-db --seed
```
is sufficient to create all tables and seed the baseline catalog and contract price tiers.

However, `Base.metadata.create_all()` only creates missing tables; it does not alter or upgrade existing SQLite table schemas when columns are added, removed, or modified.

After pulling a commit that changes ORM schema definitions (such as adding `OrderDraft.extracted_order_total_cents`), developers MUST recreate the local database before running the application:

**Windows PowerShell:**
```powershell
Remove-Item .\ordershield.db -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m app.cli init-db --seed
```

**Linux/macOS:**
```bash
rm -f ./ordershield.db
python -m app.cli init-db --seed
```

> [!WARNING]
> Do not use this reset procedure for any database containing data that must be preserved. Production-grade migration and schema versioning frameworks (e.g., Alembic) remain outside the scope of the current training/hackathon MVP.

---

## 3. Running the Application

### 3.1 Live Mode
Configure environment variables for the reconciled primary training provider (Alibaba Qwen 3.8 Flash with reasoning disabled; see ADR 0001) or alternate explicitly configured provider (Google Gemini 3.5 Flash-Lite).

When using Qwen (`LLM_PROVIDER="qwen"`), both `LLM_API_KEY` and the workspace-specific Singapore compatible-mode base URL `QWEN_BASE_URL` are required. `LLM_API_KEY` alone is not sufficient. The base URL must follow the format `https://<workspace-host>.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`.

```bash
# Windows PowerShell (Primary Live Provider: Alibaba Qwen 3.8 Flash):
$env:LLM_PROVIDER="qwen" # Primary training/demo live provider (qwen3.8-flash)
$env:LLM_API_KEY="your-dashscope-api-key"
$env:QWEN_BASE_URL="https://<workspace-host>.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"

# Windows PowerShell (Alternate Live Provider: Google Gemini 3.5 Flash-Lite):
# $env:LLM_PROVIDER="gemini"
# $env:LLM_API_KEY="your-gemini-api-key"

# Linux/macOS (Primary Live Provider: Alibaba Qwen 3.8 Flash):
export LLM_PROVIDER="qwen"
export LLM_API_KEY="your-dashscope-api-key"
export QWEN_BASE_URL="https://<workspace-host>.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"

# Linux/macOS (Alternate Live Provider: Google Gemini 3.5 Flash-Lite):
# export LLM_PROVIDER="gemini"
# export LLM_API_KEY="your-gemini-api-key"

# Launch the FastAPI server
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Open your browser to: `http://127.0.0.1:8000/static/index.html`

*Note on Provider Governance*: Provider selection is strictly configuration-driven. The system does not perform automatic runtime provider failover or silent provider substitution. Switching from primary (Qwen) to alternate provider (Gemini) requires explicit operator environment configuration and server restart. All generated drafts and audit events record the actual provider and model used.

### 3.2 Offline / Replay Mode (Deterministic Demo Path)
To establish a deterministic, offline, reproducible demo path without external network calls or API quotas:
```bash
# Launch server without external AI dependencies
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Then trigger demo fixtures via the web UI or via the dedicated fixture endpoints (`/api/v1/fixtures/...`).  
*Note*: The UI will visibly badge all screens with `⚠️ DEMO / REPLAY MODE (NON-LIVE FIXTURE DATA)`.

---

## 4. End-to-End Verification Scenarios

### Scenario 1: Clean Live Intake & One-Click Approval (Happy Path - P1)
1. **Action**: Upload `tests/fixtures/po_clean_acme.txt` via web UI or live intake API:
   ```bash
   curl -X POST "http://127.0.0.1:8000/api/v1/orders/ingest" \
     -F "file=@tests/fixtures/po_clean_acme.txt"
   ```
2. **Expected Outcome**:
   - Header extracted (`CUST-ACME`, `PO-10023`) with field-level provenance.
   - Line items extracted and mapped to SKUs with `High` confidence (`sku_resolution_source = "AI_HIGH_CONFIDENCE"`).
   - Quantity-based contract price tier selected deterministically; 0 discrepancies flagged.
   - Status: `Ready for Approval` (Green badge).
   - Click **"Approve Order"** → Status transitions to `Approved`, `VerifiedOrderRecord` created with order number `VO-2026-0001`.
   - Subsequent edits or re-approval attempts return `409 Conflict`.

---

### Scenario 2: Seeded Discrepancy & Ambiguity Handling (P2)
1. **Action**: Upload `tests/fixtures/po_discrepancy_apex.txt`:
   ```bash
   curl -X POST "http://127.0.0.1:8000/api/v1/orders/ingest" \
     -F "file=@tests/fixtures/po_discrepancy_apex.txt"
   ```
2. **Expected Outcome**:
   - Status: `Needs Review` (Amber badge). "Approve Order" button is **disabled**.
   - Line 1: `PriceMismatch` flagged (PO requested $18.00 vs contract tier price $22.00).
   - Line 2: `CatalogMatchingMismatch` flagged (`Ambiguous`, offers candidate SKUs including `SKU-WRAP-15` and `SKU-WRAP-18`).
3. **Operator Resolution**:
   - Operator selects candidate `SKU-WRAP-15` for Line 2 (`PATCH /api/v1/drafts/{draft_id}/lines/{line_2_id}` with `action="SelectSKU"`; use the `line_id` returned in the draft response, as `line_number` is not the route identifier).
   - Line 2 `sku_resolution_source` transitions to `"OPERATOR_SELECTED"`.
   - Deterministic revalidation (business rules, not AI) immediately evaluates catalog attributes and reveals `QuantityOrPackagingBreach` because customer requested quantity 2 is below catalog MOQ 5 (`min_order_quantity = 5`). Correcting quantity to bypass MOQ is prohibited because the source PO document genuinely specifies 2.
   - For Line 1, commercial price violation cannot be overridden: operator deletes the non-compliant line (`DELETE /api/v1/drafts/{draft_id}/lines/{line_1_id}`).
   - Line 1 becomes `Removed`; its discrepancy flag transitions to `ResolvedByLineRemoval` (history preserved).
4. **Final State & Explicit Rejection**:
   - Line 2 still carries the unresolved `QuantityOrPackagingBreach` discrepancy.
   - Draft status remains `Needs Review` (Amber badge); "Approve Order" remains blocked.
   - Because commercial violations cannot be resolved without altering genuine source PO facts, the operator rejects the draft:
     ```bash
     curl -X POST "http://127.0.0.1:8000/api/v1/drafts/{draft_id}/reject" \
       -H "Content-Type: application/json" \
       -d '{"operator_id": "quickstart-op", "reason": "Valid MOQ breach cannot be resolved without changing the source order."}'
     ```
   - Draft transitions to terminal `Rejected` status. Zero `VerifiedOrderRecord` is created, and a `DraftRejected` audit event is recorded.

---

### Scenario 3: AI Service Failure Resilience & Graceful Error (SC-006)
1. **Action**: Simulate AI provider failure / disconnect by uploading with an invalid API key or offline network.
2. **Expected Outcome**:
   - Immediately detectable connection, auth, or invalid key errors return HTTP 503 with a target of ≤5 seconds from request intake.
   - Stalled inference conditions are aborted at the bounded **15-second** client deadline, returning HTTP 503 per approved SC-006 amendment.
   - UI displays the HTTP 503 provider diagnostic returned by the backend (`AIProviderUnavailableError`) rather than fabricating success.
   - Zero automatic runtime failover to any alternate provider and zero silent substitution of fixture data.
   - Zero corrupted or partial drafts created (zero partial persistence).

---

### Scenario 4: Replay Mode & Visible Isolation
1. **Action**: Trigger intake via the dedicated fixture replay endpoint:
   ```bash
   curl -X POST "http://127.0.0.1:8000/api/v1/fixtures/fixture-clean-acme/ingest"
   ```
2. **Expected Outcome**:
   - Top banner prominently displays: `⚠️ DEMO / REPLAY MODE (NON-LIVE FIXTURE DATA)`.
   - API response explicitly contains `"is_replay_mode": true`.

---

### Scenario 5: Audit Trail & Grounded Provenance Inspection (P3)
1. **Action**:
   1. Approve an order using Scenario 1 and retain the returned `order_id`.
   2. In the SPA at:
      `http://127.0.0.1:8000/static/index.html`
      use the approved-order audit action ("View audit trail"),
      OR query the API directly:
      ```bash
      curl "http://127.0.0.1:8000/api/v1/orders/{order_id}"
      ```
   3. Do not substitute human-readable `order_number` (for example `VO-2026-0001`) for `{order_id}`.
2. **Expected Outcome**:
   - Displays verbatim source text snippet next to each mandatory header and line-item field with exact canonical source locations (field-level provenance).
   - Chronological audit log displays all lifecycle transitions, operator mutations (field corrections, line removals), and the approval event.

---

## 5. Automated Test Suite Execution

Run the complete automated test suite verifying unit arithmetic, contract validation, and API contracts:

```bash
# Run all tests
pytest -v

# Run deterministic reconciliation engine tests (zero AI dependency, integer cents math)
pytest tests/unit/test_reconciliation.py -v

# Run quantity-based contract tier tests
pytest tests/unit/test_pricing_tiers.py -v

# Run API contract, terminal state, and failure-mode tests
pytest tests/integration/test_api_contracts.py -v
```
All tests must pass without errors.
