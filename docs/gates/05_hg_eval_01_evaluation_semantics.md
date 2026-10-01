# Human Gate Decision Record: HG-EVAL-01

- **Gate ID**: `HG-EVAL-01`
- **Topic**: OrderShield Evaluation Protocol, Ground-Truth Manifest, Results Schema, and SC-001 Dual Measurement Framework
- **Decider**: Project Brain (ChatGPT / Human Lead)
- **Executor**: Fast Executor (Antigravity) / Integrator (Vladimir)
- **Date**: 2026-10-02
- **Base Artifacts**:
  - `docs/evaluation/protocol.md` (Revision 1.1.0)
  - `docs/evaluation/ground_truth_manifest.json` (Manifest 1.1.0)
  - `docs/evaluation/results.schema.json`
  - `docs/evaluation/fixtures/sc001_prepared_5line_po.txt`
- **Decision Outcome**: **APPROVED**

---

## 1. Questions for Decision & Reconciliation Summary

### Question 1: Are the metric formulas and denominators accepted?
- **Decision**: **APPROVED**.
- AI extraction metrics AI-1 (Schema Validity) and AI-2 (Mandatory Field Exactness) are designated **REPORT ONLY** (preventing invented pass/fail thresholds).
- Frozen normative targets are preserved:
  - SC-002: 100% seeded discrepancy catch rate
  - SC-003: 0 wrong-confident SKUs / 100% review routing
  - SC-004: 100% provenance citation validity
  - SC-005: 0% approval leakage (100% gate blocking)
  - SC-006: failure / timeout bounds ($\le 5\text{ s}$ immediately detectable, $\le 15\text{ s}$ live budget, 0 silent fallback, visible `is_replay_mode` badging)

### Question 2: Are the ambiguous/out-of-catalog test cases accepted as valid measures of SC-003?
- **Decision**: **APPROVED**.
- Historical review traps (`h05_no_size`, `h06_no_pack`, `h09_latex`) are evaluated strictly at the **AI boundary** (abstaining from commitment, returning expected review state, 0 wrong-confident SKUs).
- Application review traps (`ambiguous_apex`, `discrepancy_apex` State A) are evaluated at the **reconciliation boundary** where ambiguous descriptions trigger `CatalogMatchingMismatch`.

### Question 3: Is the claims policy strict enough?
- **Decision**: **APPROVED**.
- Strictly prohibits ungrounded marketing claims (`"15 minutes manually"`, `"$400 saved"`, `"2–4% margin loss"`, `"production accuracy"`).
- Explicitly mandates that demo figures be labeled as **Prepared Demo Scenario Measurements** rather than enterprise statistical generalities.
- Enforces core invariant: *"Test-suite PASS proves implementation conformance but does not by itself constitute an empirical benchmark PASS."*

### Question 4: Is the manual baseline design acceptable?
- **Decision**: **APPROVED**.
- 2–3 participants characterized as a *"small convenience sample performing a standardized synthetic reconciliation task"* with explicit recorded limitations.
- Standardized inputs: identical source POs (including the dedicated 5-line fixture `sc001_prepared_5line_po.txt`), catalog sheet, contract pricing sheet, allowed tools (calculator + text editor/spreadsheet), and standardized start/stop timing rules.

### Question 5: SC-001 Dual Measurement Framework & Stateful Discrepancies
- **Decision**: **APPROVED**.
- Authored canonical 5-line PO fixture at `docs/evaluation/fixtures/sc001_prepared_5line_po.txt`.
- Formally separated `automated_system_path_duration` (diagnostic runtime velocity) from `assisted_operator_completion_duration` (actual human coordinator using OrderShield, required for SC-001 PASS/FAIL).
- Automated CLI runner alone cannot mark SC-001 PASS (`PARTIALLY_MEASURED` after runner; `PASS`/`FAIL` decided upon assisted measurement).
- Stateful discrepancy emergence modeled for `discrepancy_apex`: State A (intake: PriceMismatch + CatalogMatchingMismatch; MOQ latent) $\to$ State B (operator SelectSKU reveals QuantityOrPackagingBreach) $\to$ State C (rejection). Total 2 operator actions.
- Canonical SelectSKU endpoint verified as `PATCH /api/v1/drafts/{draft_id}/lines/{line_id}` with body `{"action": "SelectSKU", "matched_sku": "..."}`.
- Canonical not-ready approval rejection verified as HTTP 409 `DraftNotReadyForApprovalError` (`"Draft is not Ready for Approval"`).
- Canonical terminal draft immutability verified as HTTP 409 `TerminalDraftConflictError` (`"Cannot modify an Approved or Rejected draft"`).

---

## 2. Authorization
The evaluation protocol, ground-truth manifest, and results schema are formally approved. Implementation of **VLD-EVAL-02** (Reproducible Evaluation Runner) is authorized to proceed.
