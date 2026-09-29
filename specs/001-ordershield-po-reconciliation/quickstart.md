# Quickstart & Verification Guide: OrderShield

**Feature Branch**: `001-ordershield-po-reconciliation`  
**Date**: 2026-09-29  
**Status**: Ready for Planning (Human Gate Final Reconciliation)  

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
python -m app.cli init-db --seed
```

---

## 3. Running the Application

### 3.1 Live Mode
Configure environment variables for your selected AI provider (OpenAI is shown here as an illustrative example of a supported backend):
```bash
# Windows PowerShell (Example):
$env:LLM_PROVIDER="openai" # Example provider
$env:LLM_API_KEY="your-api-key"
# Linux/macOS (Example):
export LLM_PROVIDER="openai"
export LLM_API_KEY="your-api-key"

# Launch the FastAPI server
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Open your browser to: `http://127.0.0.1:8000`

### 3.2 Offline / Replay Mode (Deterministic Demo Path)
To establish a deterministic, offline, reproducible demo path without external network calls or API quotas:
```bash
# Launch server without external AI dependencies
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Then trigger demo fixtures via the web UI or via the dedicated fixture endpoints (`/api/v1/fixtures/...`).  
*Note*: The UI will visibly badge all screens with `[DEMO / REPLAY MODE (NON-LIVE)]`.

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
   - Line 2: `CatalogMatchingMismatch` flagged (`Ambiguous`, offers 2 candidate SKUs).
3. **Operator Resolution**:
   - Operator selects candidate SKU for Line 2 or searches catalog via `GET /api/v1/catalog?query=...` (`PATCH /api/v1/drafts/{id}/lines/2` with `action="SelectSKU"`).
   - Line 2 `sku_resolution_source` becomes `"OPERATOR_SELECTED"`.
   - For Line 1, commercial price violation cannot be overridden: operator deletes the non-compliant line (`DELETE /api/v1/drafts/{id}/lines/1`).
   - Line 1 becomes `Removed`; its discrepancy flag transitions to `ResolvedByLineRemoval` (history preserved).
   - Unresolved discrepancies drop to 0 → Draft automatically transitions to `Ready for Approval` (Green badge).
   - Click **"Approve Order"** → Order committed.

---

### Scenario 3: AI Service Failure Resilience & Graceful Error (SC-006)
1. **Action**: Simulate AI provider timeout / disconnect by uploading with an invalid API key or offline network.
2. **Expected Outcome**:
   - System returns HTTP 503 within **5 seconds** (enforced by client timeout ≤4.5s).
   - UI displays clear diagnostic banner: *"External AI extraction service failed: request timed out. Live inference failed; fixture data was not silently substituted."*
   - Zero corrupted or partial drafts created.

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
1. **Action**: Navigate to `http://127.0.0.1:8000/orders/VO-2026-0001` or run:
   ```bash
   curl "http://127.0.0.1:8000/api/v1/orders/VO-2026-0001"
   ```
2. **Expected Outcome**:
   - Displays verbatim source text snippet next to each mandatory header and line-item field with exact canonical text offsets.
   - Chronological audit log displays all lifecycle transitions, operator field corrections, line removals, and approval timestamp.

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
