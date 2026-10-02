# Human Gate Decision Record: HG-ADVISORY-01

- **Gate ID**: `HG-ADVISORY-01`
- **Topic**: Read-Only Advisory Inspection API (`GET /api/v1/drafts/{draft_id}/advisory`), Pydantic Contract, Grounded Trace, and LLM Schema Authority
- **Decider**: Project Brain (ChatGPT / Human Lead)
- **Executor**: Fast Executor (Antigravity)
- **Date**: 2026-10-02
- **Base Artifacts**:
  - `app/models/advisory.py` (Pydantic schema with `ADVISORY_SCHEMA_VERSION = "1.0.0"`)
  - `app/services/advisory.py` (Read-only assembly & sandbox simulation)
  - `app/api/routes_drafts.py` (FastAPI route with strict query validation)
  - `docs/recon/counterfactual_operator_actions.md`
- **Decision Outcome**: **APPROVED**

---

## 1. Context & Architecture Decision

The order draft reconciliation process requires exposing advisory diagnostic bundles (baseline blockers, counterfactual dry-run plans, fuzzy review priority, fuzzy SKU match confidence, and grounded evidence trace) to downstream LLM and operator assistance systems without risking state mutations or leaky business rules.

### Key Decisions:

1. **Strictly Read-Only & Zero Database Mutations**:
   - The endpoint `GET /api/v1/drafts/{draft_id}/advisory` is strictly safe and idempotent.
   - Prohibits `flush`, `commit`, `rollback`, row insertions, row deletions, or ORM attribute mutations on the request session.
   - All simulations (including fresh baseline status and counterfactual search) execute exclusively inside ephemeral in-memory SQLite sandboxes (`sqlite:///:memory:`) that are disposed immediately.
   - Caller identity map and uncommitted pending session writes are preserved under `db.no_autoflush`.

2. **Pydantic Contract as Canonical Machine-Readable Authority (Non-File Contract)**:
   - By decision of the project lead, the advisory response contract is **not** authored as a static repository file under `contracts/` or in `specs/001-ordershield-po-reconciliation/contracts/api-contracts.md`.
   - The frozen `specs/001-ordershield-po-reconciliation/contracts/api-contracts.md` file remains intentionally untouched and preserved in its original P1/P2 baseline state.
   - The authoritative contract is the strict Pydantic model `DraftAdvisoryResponse` with `ConfigDict(extra="forbid")`, field-level `description` metadata, and published `/openapi.json`.
   - Drift prevention is guaranteed through automated drift-guard unit tests in `tests/unit/test_advisory_schema.py`.

3. **Schema Versioning & Stability (`schema_version="1.0.0"`)**:
   - `schema_version` is fixed as a literal constant (`const` in JSON Schema).
   - SemVer rules govern future evolution:
     - Patch bump (`1.0.x`): non-semantic documentation and field descriptions.
     - Minor bump (`1.x.0`): strictly additive optional fields or informational trace stages.
     - Major bump (`x.0.0`): any field removal, renaming, type narrowing, or changes to `extra="forbid"` models.

4. **Section Bundle Filtering**:
   - Supports query parameter `sections` for arbitrary subsets of `{"baseline", "counterfactuals", "review_priority", "sku_confidence", "trace"}`.
   - Omitted sections return `null` with keys preserved to maintain structural predictability for LLM consumers.
   - Bounded search parameters `max_depth` (1..3) and `max_scenarios` (1..50) are strictly validated. When `counterfactuals` is omitted from `sections`, counterfactual simulations are bypassed completely.

5. **Verifiable Grounded Trace**:
   - Trace evidence citations are restricted to three authoritative sources:
     - `document`: verbatim snippet and character coordinates verified directly against canonical document raw text;
     - `reference_data`: master catalog and customer contract pricing tiers from database;
     - `derived`: deterministic arithmetic and aggregation formulas.

---

## 2. Authorization

The implementation of `GET /api/v1/drafts/{draft_id}/advisory`, `app/models/advisory.py`, `app/services/advisory.py`, and comprehensive test suites is approved.
