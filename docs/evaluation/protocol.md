# OrderShield Evaluation Protocol & Ground-Truth Contract

- **Document ID**: `EVAL-PROTO-01`
- **Revision**: 1.1.0 (Reconciled with canonical runtime contracts)
- **Status**: Draft / Submitted for Human Gate Review (`HG-EVAL-01`)
- **Author / Lead**: Vladimir (Integrator / Project Brain)
- **Canonical Date**: 2026-10-02
- **Related Specifications**: `specs/001-ordershield-po-reconciliation/spec.md`
- **Related Decisions**: `docs/decisions/0001-training-live-ai-provider-selection.md` (ADR 0001)
- **Manifest**: `docs/evaluation/ground_truth_manifest.json`
- **Results Schema**: `docs/evaluation/results.schema.json`
- **Dedicated SC-001 Fixture**: `docs/evaluation/fixtures/sc001_prepared_5line_po.txt`

---

## 1. Executive Summary & Foundational Invariants

### 1.1 Purpose
This protocol defines a rigorous, repeatable, and auditable evaluation methodology for OrderShield. It converts system specification criteria (SC-001 through SC-006), architectural boundaries, and performance claims into exact mathematical formulas, standard test procedures, and machine-readable contracts.

### 1.2 Non-Claim Invariant & Status Policy
**This document asserts NO empirical benchmark claims.**
- All Success Criteria (SC-001 through SC-006) are initialized to the pre-execution state **`NOT_YET_MEASURED`**.
- Performance numbers, latency percentiles, accuracy rates, and operational comparisons may only be asserted after the execution of the evaluation runner (`VLD-EVAL-02`) and manual baseline study (`VLD-EVAL-03`), with raw run logs committed to `docs/evaluation/results/`.
- **Implementation Invariant**: Test-suite PASS proves implementation conformance but does not by itself constitute an empirical benchmark PASS.

### 1.3 System Boundary & Division of Responsibilities
In accordance with `AGENTS.md` and ADR 0001:
- **AI Component**: Strictly bounded to probabilistic interpretation (unstructured document intake, fuzzy field extraction, candidate SKU semantic ranking, and ambiguity detection). The AI component is untrusted; its output must validate against a strict schema and grounded source citations.
- **Deterministic Rules Engine**: Strictly responsible for all business arithmetic, contract price tier resolution, minimum order quantity (MOQ) checks, packaging increment validation, discrepancy generation, draft status progression, and immutable audit logging.
- **Human Operator**: Sole entity authorized to resolve discrepancies (via source-grounded field edits or explicit SKU candidate selection), remove invalid lines, or reject/approve order drafts.

---

## 2. Test Corpora Architecture

Evaluation leverages frozen, version-controlled repository assets defined in `docs/evaluation/ground_truth_manifest.json`.

```text
                                Evaluation Corpora
                                        │
         ┌──────────────────────────────┼──────────────────────────────┐
         ▼                              ▼                              ▼
Historical Bake-Off Corpus      App MVP Reconciliation Corpus    Synthetic Rule Suite
  - 10 cases (h01..h10)           - sc001_prepared_5line (5 lines) - Arithmetic errors
  - Frozen spike schema           - clean_acme (2 lines)           - Packaging breaches
  - AI-boundary review traps      - discrepancy_apex (stateful)    - Unrecognized SKU traps
  - Multi-provider evidence       - ambiguous_apex (1 line)        - Gate 409 rejections
                                  - unextractable_pdf (damaged)
```

### 2.1 Historical Bake-off Corpus (`h01` through `h10`)
Reused directly from `spikes/ordershield/live/fixtures/` and `spikes/ordershield/live/expected.json` against `spikes/ordershield/catalog.json`:
- **Determinate Clear Cases**: `h01_paper` (clear SKU match), `h02_large` (catalog description match), `h03_alias` (semantic alias match), `h04_tape` (tape width/length match).
- **Semantic & Dangerous Negation Traps**:
  - `h07_not_medium`: Requests large gloves; explicitly states "NOT medium". Must resolve to `GLOVE-N-L`.
  - `h08_not_large`: Requests medium gloves; explicitly states "NOT large". Must resolve to `GLOVE-N-M`.
- **Review Traps (AI Boundary Evaluation)**:
  - `h05_no_size`: Powderless nitrile gloves with omitted size. AI must abstain from confident commitment, returning `expected_sku = null` and `needs_sku_review = true` (`HUMAN_REVIEW`).
  - `h06_no_pack`: Medium nitrile gloves with missing pack count attribute. AI must abstain from confident commitment (`HUMAN_REVIEW`).
  - `h09_latex`: Requests latex exam gloves. Catalog exclusively stocks nitrile. AI must abstain from confident commitment (`HUMAN_REVIEW`).
- **Damaged & Incomplete Input**:
  - `h10_damaged`: Truncated/damaged text lacking customer name, quantity, and price. Must extract valid nulls with null provenance, resolving SKU where description permits, but marking for human review due to missing mandatory header/line fields.

### 2.2 Application MVP Reconciliation Corpus
Committed application fixtures evaluated against active database master data (`app.cli.BASELINE_PRODUCTS` and `BASELINE_TIERS`):
- **`sc001_prepared_5line`** (`docs/evaluation/fixtures/sc001_prepared_5line_po.txt`): Dedicated synthetic 5-line purchase order for Acme Industrial Supplies (`CUST-ACME`). 0 discrepancies, high-confidence SKU mapping, valid integer-cents arithmetic, total $2,212.50. Evaluates end-to-end processing velocity under SC-001 and serves as the standardized test case for the manual baseline study.
- **`clean_acme`** (`tests/fixtures/po_clean_acme.txt`, `app/fixtures/clean_acme.json`): 2-line purchase order for Acme Industrial Supplies (`CUST-ACME`). 0 discrepancies, high-confidence SKU mapping, valid arithmetic ($350.00). Evaluates clean intake workflow.
- **`discrepancy_apex`** (`tests/fixtures/po_discrepancy_apex.txt`, `app/fixtures/discrepancy_apex.json`): Stateful 2-line purchase order for Apex Distribution (`CUST-APEX`). Demonstrates sequential discrepancy emergence:
  - *State A (Intake)*: Line 1 exhibits `PriceMismatch` ($18.00 requested vs $22.00 contract tier for qty 10); Line 2 exhibits `CatalogMatchingMismatch` (ambiguous description "Standard pallet wrap"). The MOQ rule on Line 2 remains latent/inactive because line 2 has not yet been resolved to a catalog SKU.
  - *State B (Operator SKU Selection)*: Operator selects candidate `SKU-WRAP-15` on Line 2 via `PATCH /api/v1/drafts/{draft_id}/lines/{line_id}` with body `{"action": "SelectSKU", "matched_sku": "SKU-WRAP-15"}`. `CatalogMatchingMismatch` is resolved; deterministic revalidation reveals `QuantityOrPackagingBreach` because requested quantity $2 < \text{MOQ } 5$.
  - *State C (Rejection)*: Draft cannot transition to `Ready for Approval` or `Approved` due to commercial price breach and MOQ violation; operator rejects draft with recorded reason.
- **`ambiguous_apex`** (`tests/fixtures/po_ambiguous_apex.txt`, `app/fixtures/ambiguous_apex.json`): 1-line purchase order for Apex Distribution. Seeded `CatalogMatchingMismatch` (ambiguous description "Standard pallet wrap", qty 10). Resolves to clean draft upon operator SKU selection (`SKU-WRAP-15`) via `PATCH /api/v1/drafts/{draft_id}/lines/{line_id}` with body `{"action": "SelectSKU", "matched_sku": "SKU-WRAP-15"}`.
- **`unextractable_pdf`** (`tests/fixtures/po_unextractable.pdf`): Digital PDF with corrupted/unextractable text stream. Intake returns HTTP `400` with error type `UnextractableTextError`, 0 provider invocations, 0 partial draft persistence, and 0 fixture fallback.

### 2.3 Synthetic Deterministic Rule Suite
Synthetic edge-case fixtures verifying discrete arithmetic and packaging rules:
- Line-level arithmetic error (`quantity * unit_price != line_total`).
- Order-level arithmetic error (`sum(line_totals) != order_total`).
- Packaging increment breach (requested quantity not an integer multiple of package increment).
- Unrecognized description (zero catalog candidate matches).
- Gate enforcement: `POST /api/v1/drafts/{draft_id}/approve` on an unreviewed or discrepancy-laden draft strictly returns HTTP `409` Conflict, blocking approval and preventing illegal state transitions.

---

## 3. Metric Definitions & Mathematical Semantics

### 3.1 Schema Separation Principle
The evaluation framework strictly separates metrics between historical provider research and current application runtime contracts:

```text
                  Schema Separation Architecture
                                │
        ┌───────────────────────┴───────────────────────┐
        ▼                                               ▼
Historical Bake-off Metrics                     Current App Metrics
(spikes/ordershield/**)                         (app.models.schemas)
- Fields: currency, sale_unit,                  - Fields: customer_name, po_number,
  requested_quantity, customer_unit_price,        extracted_order_total, customer_description,
  customer_stated_total                           extracted_quantity, extracted_unit_price,
- AI-boundary review routing                      extracted_line_total, matched_sku, sku_confidence
- Reproduces historical bake-off                - LocationDataSchema provenance
                                                - Canonical API contracts (is_replay_mode)
```

The runner must never silently map or conflate these schemas.

---

### 3.2 AI Extraction & Grounding Metrics

#### Metric AI-1: Schema-Valid Extraction Rate *(REPORT ONLY)*
- **Objective**: Measure the proportion of attempted inference requests returning well-formed JSON conforming to the applicable target schema.
- **Status**: **REPORT ONLY**. No invented normative threshold ($\ge 99\%$) is enforced as an MVP pass/fail gate.
- **Calculation**:
  $$\text{Schema-Valid Rate} = \frac{N_{\text{valid}}}{D_{\text{attempted}}}$$
- **Numerator ($N_{\text{valid}}$)**: Count of provider calls where raw response text parses as valid JSON and satisfies schema validation with 0 errors.
- **Denominator ($D_{\text{attempted}}$)**: Total attempted inference requests dispatched to the provider.
- **Excluded Cases**: Calls blocked locally prior to HTTP dispatch (e.g. missing API keys).
- **Failure Handling**: Network timeouts, HTTP 4xx/5xx, or empty responses earn 0 in the numerator and count in the denominator.

#### Metric AI-2: Mandatory Field Exactness *(REPORT ONLY)*
- **Objective**: Measure exact string-level extraction accuracy of core business fields compared to ground truth.
- **Status**: **REPORT ONLY**. No invented normative threshold ($\ge 95\%$) is enforced as an MVP pass/fail gate.
- **Fields Evaluated**:
  - *Current App Schema*: `customer_name`, `po_number`, `extracted_order_total` (when present), `customer_description`, `extracted_quantity`, `extracted_unit_price`, `extracted_line_total`.
  - *Historical Spike Schema*: `customer_name`, `po_number`, `currency`, `customer_stated_total`, `customer_description`, `requested_quantity`, `sale_unit`, `customer_unit_price`.
- **Comparison Rule**: Exact string equality after whitespace collapsing (`" ".join(v.split())`). Numbers normalized to standard decimal representation (e.g. `"25.00"`). Null strictly equals null.
- **Calculation**:
  $$\text{Field Exactness} = \frac{\sum_{i=1}^{M} \mathbb{I}(\text{extracted}_i = \text{ground\_truth}_i)}{M}$$
  where $M$ is total expected field instances across all evaluated documents.

#### Metric AI-3: Provenance Citation Validity Rate *(Normative: 100% for SC-004)*
- **Objective**: Ensure extracted fields are verifiably grounded in the source text, preventing hallucinated numbers or identifiers.
- **Rule**:
  - If field is non-null: `verbatim_snippet` must be non-empty, must exist as an exact substring in the source document, and character/line offsets must point to the snippet location.
  - If field is null: `verbatim_snippet` must be null or empty string (valid missingness).
- **Calculation**:
  $$\text{Provenance Validity} = \frac{N_{\text{grounded\_fields}}}{D_{\text{evaluated\_fields}}}$$
- **Normative Target**: $100.0\%$ (SC-004).

#### Metric AI-4: Determinate SKU Accuracy
- **Objective**: Measure SKU matching accuracy exclusively on clear, determinate order lines where a catalog SKU can be unambiguously resolved.
- **Calculation**:
  $$\text{Determinate SKU Accuracy} = \frac{N_{\text{correct\_sku}}}{D_{\text{determinate\_lines}}}$$
- **Numerator ($N_{\text{correct\_sku}}$)**: Count of determinate lines where `matched_sku == expected_sku` and `sku_confidence == "High"`.
- **Denominator ($D_{\text{determinate\_lines}}$)**: Total determinate lines across evaluated documents.
- **Abstention Handling**: A conservative abstention (`matched_sku = null` with `sku_confidence = Ambiguous`) earns 0 in this numerator, but is logged as a safe abstention rather than a false match.

#### Metric AI-5: Ambiguous / Unrecognized Review-Routing Rate *(Normative: 100% for SC-003)*
- **Objective**: Measure the system's ability to catch ambiguous descriptions, missing attributes, or out-of-catalog items and route them to human review.
- **Evaluation Semantics**:
  - *Historical Bake-off*: Evaluated strictly at the **AI boundary**. Successful when the provider abstains from confident commitment, returns `needs_sku_review = true`, and produces 0 wrong-confident SKUs.
  - *Current App Reconciliation*: Evaluated at the **Reconciliation boundary**. Successful when the line produces a `CatalogMatchingMismatch` discrepancy and sets draft status to `Needs Review`.
- **Calculation**:
  $$\text{Review-Routing Rate} = \frac{N_{\text{routed\_to\_review}}}{D_{\text{trap\_lines}}}$$
- **Normative Target**: $100.0\%$ (SC-003).

#### Metric AI-6: Wrong-Confident SKU Count & Rate *(Normative: 0 for SC-003)*
- **Objective**: Measure catastrophic false commitments where the system confidently assigns an incorrect SKU or assigns a SKU to an ambiguous/out-of-catalog item.
- **Definition**: Any instance where `sku_confidence == "High"` AND:
  - The line is determinate, but `matched_sku != expected_sku`; OR
  - The line is a review trap / out-of-catalog item, but a SKU was confidently assigned.
- **Calculation**:
  $$\text{Wrong-Confident Rate} = \frac{N_{\text{wrong\_confident}}}{D_{\text{total\_lines}}}$$
- **Normative Target**: **Strictly 0 (0.0%)** (SC-003).

#### Metric AI-7: Provider Latency Distribution *(Normative: SC-006 & ADR 0001)*
- **Objective**: Measure end-to-end HTTP wall-clock elapsed time for live inference requests.
- **Metrics Computed**: Minimum, median ($p50$), mean, $p90$, $p95$, and maximum latency in seconds.
- **Separation Policy**:
  - `latency_success_distribution`: Latency distribution of completed, schema-valid inference requests.
  - `latency_all_attempts_distribution`: Latency distribution including aborted timeouts and HTTP errors.
- **Normative Target**:
  - Immediately detectable errors $\le 5.0\text{ s}$ from request intake (SC-006).
  - Healthy live inference bounded within $\le 15.0\text{ s}$ budget (ADR 0001).

---

### 3.3 Deterministic Reconciliation Metrics

#### Metric DET-1: Seeded Discrepancy Catch Rate *(Normative: 100% for SC-002)*
- **Objective**: Verify that 100% of seeded pricing mismatches, arithmetic errors, MOQ violations, and catalog matching discrepancies are detected by the rules engine.
- **Calculation**:
  $$\text{Catch Rate} = \frac{N_{\text{discrepancies\_detected}}}{D_{\text{discrepancies\_seeded}}}$$
- **Condition**: Discrepancy detected with matching category (`PriceMismatch`, `QuantityOrPackagingBreach`, `ArithmeticMismatch`, or `CatalogMatchingMismatch`), attached to correct line, with `resolution_state == "Unresolved"` and `severity == "Blocking"`.
- **Stateful Accounting**: In stateful fixtures (`discrepancy_apex`), latent discrepancies (such as MOQ breach on an unresolved SKU) are evaluated at the appropriate lifecycle state (State B after SKU selection), not falsely expected at initial intake.
- **Normative Target**: $100.0\%$ (SC-002).

#### Metric DET-2: False-Positive Discrepancy Rate
- **Objective**: Verify that clean drafts do not generate spurious discrepancy flags.
- **Calculation**:
  $$\text{False-Positive Rate} = \frac{N_{\text{spurious\_flags}}}{D_{\text{clean\_lines}}}$$
- **Normative Target**: Strictly 0 flags on clean inputs ($0.0\%$).

#### Metric DET-3: Commercial-Rule Enforcement Rate
- **Objective**: Verify exact integer-cents contract tier price selection, MOQ gating, and packaging increment division.
- **Verification Rule**: 100% agreement with precomputed contract matrices without floating-point intermediate rounding. Rejection of direct commercial price/quantity modifications by operators.
- **Normative Target**: $100.0\%$.

#### Metric DET-4: Unresolved-Discrepancy Approval Blocking Rate *(Normative: 100% for SC-005)*
- **Objective**: Ensure no order draft with unresolved discrepancies or incomplete mandatory fields can transition to `Approved`.
- **Canonical API Route**:
  $$\text{POST } /api/v1/drafts/\{draft\_id\}/approve$$
- **Canonical Behavior**:
  - Returns HTTP `409` (Conflict).
  - Error: `DraftNotReadyForApprovalError`.
  - Message: `"Draft is not Ready for Approval"`.
  - Effects:
    - Creates 0 `VerifiedOrder` records.
    - Draft does not transition to `Approved`.
    - Draft remains in its valid pre-approval state (`Needs Review`).
- **Calculation**:
  $$\text{Gate Blocking Rate} = \frac{N_{\text{blocked\_approvals}}}{D_{\text{invalid\_approval\_attempts}}}$$
- **Normative Target**: $100.0\%$ (0% approval leakage) (SC-005).

#### Metric DET-5: Terminal-State Immutability Rate
- **Objective**: Ensure committed orders (`Approved` or `Rejected`) cannot be modified, re-evaluated, or re-transitioned.
- **Canonical API Behavior**:
  - Mutation or re-transition attempts on `Approved` or `Rejected` drafts return HTTP `409` Conflict.
  - Error: `TerminalDraftConflictError`.
  - Message: `"Cannot modify an Approved or Rejected draft"`.
- **Normative Target**: $100.0\%$ rejection with canonical HTTP 409 error.

---

### 3.4 End-to-End Operational Metrics

- **Processing Duration**: Wall-clock time elapsed from document ingestion request to terminal order commitment (`Approved` or `Rejected`).
- **Operator Action Count**: Number of discrete operator interactions required to achieve terminal state:
  - Clean scenario (`sc001_prepared_5line`, `clean_acme`): 1 action (operator visual inspection and final approval sign-off).
  - Ambiguous scenario (`ambiguous_apex`): 2 actions (manual candidate SKU selection + final approval sign-off).
  - Unresolvable discrepancy stateful path (`discrepancy_apex` State A $\to$ B $\to$ C): 2 actions (SelectSKU on line 2 to reveal latent MOQ breach, followed by rejection with mandatory recorded reason).
- **Replay Transparency Contract**:
  - Replay-created draft and order representations expose `is_replay_mode: true` where that field belongs to the canonical API representation.
  - Relevant UI views displaying replay-created state visibly show the non-live replay badge.
  - A live intake request must never silently become replay/fixture execution.
  - Unrelated responses (such as fixture listings, product catalogs, or general errors) do not contain `is_replay_mode`.

---

## 4. Success Criteria Traceability Matrix

### 4.1 SC-001 Dual Measurement Framework
SC-001 specifies: *"An operations coordinator can complete end-to-end reconciliation and approval of a prepared 5-line PO in under 60 seconds."*
Because an automated CLI runner alone cannot prove human operator completion, evaluation establishes two distinct measurements:
1. **`automated_system_path_duration`**: Machine/runtime measurement of end-to-end ingestion and reconciliation processing duration. Provides diagnostic velocity profiling; does NOT independently satisfy SC-001.
2. **`assisted_operator_completion_duration`**: Measurement of an actual human operator using OrderShield to reconcile and approve the canonical 5-line fixture (`sc001_prepared_5line_po.txt`). Required to decide SC-001 PASS/FAIL.
   - **Timer Start**: Operator initiates submission of the prepared 5-line PO in the OrderShield UI.
   - **Timer Stop**: Successful approval is completed and the Verified Order record is visibly returned.
   - Operator visual inspection of the populated reconciliation view is included within this timed interval.

**SC-001 Status Lifecycle**:
- Pre-execution / unrun: **`NOT_YET_MEASURED`**
- Automated runner completed without human-assisted timing: **`PARTIALLY_MEASURED`**
- Assisted operator run completed in $<60\text{ s}$: **`PASS`**
- Assisted operator run completed in $\ge 60\text{ s}$: **`FAIL`**

An automated runner execution alone must **never** mark SC-001 as `PASS`. Assisted timing evidence is collected during manual baseline measurement (`VLD-EVAL-03`).

### 4.2 Traceability Table

| Success Criterion | Specification Requirement | Mapped Metric | Evaluation Assets / Evidence | Pre-Execution Status |
|:---|:---|:---|:---|:---:|
| **SC-001** | Prepared Demo Processing Velocity: Operations coordinator completes reconciliation & approval of prepared 5-line PO in $<60\text{ s}$. | Assisted Operator Duration (`assisted_operator_completion_duration`) & System Velocity (`automated_system_path_duration`) | `docs/evaluation/fixtures/sc001_prepared_5line_po.txt` (`sc001_prepared_5line`). Canonical 5-line fixture authored; awaiting runner timing & assisted operator execution. | **`NOT_YET_MEASURED`** *(Target: assisted $<60\text{ s}$)* |
| **SC-002** | Discrepancy Catch Rate: 100% of seeded pricing mismatches, arithmetic errors, and MOQ violations detected before approval. | `DET-1` Seeded Discrepancy Catch Rate | `discrepancy_apex.json` (stateful), `synthetic_deterministic_suite`, unit test suites (`test_reconciliation.py`). | **`NOT_YET_MEASURED`** *(Target: 100.0%)* |
| **SC-003** | Zero Hallucinated Commitments: 100% of ambiguous/out-of-catalog items routed to review; zero unrecognized descriptions assigned to unverified SKUs. | `AI-5` Review-Routing Rate & `AI-6` Wrong-Confident SKUs | Traps `h05`, `h06`, `h09`; `ambiguous_apex.json`; `test_ai_matcher.py`. | **`NOT_YET_MEASURED`** *(Target: 100% routed, 0 wrong-confident)* |
| **SC-004** | Full Provenance Visibility: 100% of extracted line items provide visible textual grounding citations back to source PO text. | `AI-3` Provenance Citation Validity Rate | `app/fixtures/*.json`, `expected.json`, `test_schemas.py`. | **`NOT_YET_MEASURED`** *(Target: 100.0%)* |
| **SC-005** | Mandatory Gate Enforcement: 0% of unreviewed or discrepancy-laden drafts can transition to committed order record without operator sign-off/resolution. | `DET-4` Approval Gate Blocking Rate (`POST /api/v1/drafts/{draft_id}/approve` $\to 409$) | `test_order_service.py`, `test_api_contracts.py`. | **`NOT_YET_MEASURED`** *(Target: 0% approval leakage)* |
| **SC-006** | Explicit Failure & Replay Transparency: Immediate diagnostic error $\le 5\text{ s}$ target; healthy live inference $\le 15\text{ s}$ budget; silent/stalled aborted at $\le 15\text{ s}$; 0 silent fallback; 100% visible replay badge. | `AI-7` Latency Profile, Replay Mode Badge Presence, `po_unextractable.pdf` HTTP 400 | `po_unextractable.pdf`, `test_ai_provider.py`, `test_api_wiring.py`. | **`NOT_YET_MEASURED`** *(Target: Immediate $\le 5\text{ s}$, Live $\le 15\text{ s}$, 0 fallback)* |

> [!IMPORTANT]
> **Pre-Execution Invariant**: Every Success Criterion is strictly marked **`NOT_YET_MEASURED`** until the evaluation runner (`VLD-EVAL-02`) and assisted timing study (`VLD-EVAL-03`) execute against the ground-truth manifest and record timestamped, verifiable result artifacts in `docs/evaluation/results/`. Implementation conformance tests prove architectural presence but do not replace empirical evaluation.

---

## 5. Manual Baseline Study Protocol (Before/After Comparison)

To validate operational acceleration without relying on ungrounded claims, this protocol specifies a small-sample, reproducible human baseline experiment.

### 5.1 Objective
Measure the baseline duration, cognitive burden, and error rate of human operators performing manual purchase order reconciliation without OrderShield.

### 5.2 Cohort Design & Limitations
- **Cohort Size**: 2 to 3 independent participants.
- **Participant Profile**: Small convenience sample performing a standardized synthetic reconciliation task.
- **Explicit Limitation**: The sample does not establish industry-wide operator performance or universal commercial baselines; it provides an empirical, auditable point of comparison for the evaluated scenarios.
- **Independence**: Participants execute the benchmark independently without collaboration, assistance, or prior knowledge of the test order solutions.

### 5.3 Benchmark Inputs Provided to Participant
Each participant is provided with identical materials:
1. **Source Documents**:
   - Case A: Clean 5-line purchase order (`docs/evaluation/fixtures/sc001_prepared_5line_po.txt`).
   - Case B: Multi-discrepancy 2-line purchase order (`po_discrepancy_apex.txt`).
   - Case C: Ambiguous description 1-line purchase order (`po_ambiguous_apex.txt`).
2. **Product Master Catalog Sheet**: Listing SKU code, product title, unit of measure, base price, MOQ, and packaging increments.
3. **Customer Contract Pricing Sheet**: Listing customer contract terms, authorized SKUs, and volume tier price breaks.
4. **Reconciliation Decision Template**: Form where the participant records matched SKU, verified unit price, total line price, discrepancies discovered, and final Approve/Reject recommendation.

### 5.4 Allowed Tools & Boundaries
- Standard desktop calculator or basic spreadsheet (e.g. Excel/Google Sheets).
- Text editor or PDF viewer for reading source documents.
- **Prohibited**: Automated reconciliation scripts, LLM tools (ChatGPT, Claude, etc.), or OrderShield software.

### 5.5 Execution & Timing Rules
- **Timer Start**: Precise timestamp recorded when the participant opens the source purchase order document and pricing sheets.
- **Timer Stop**: Precise timestamp recorded when the participant submits the completed Reconciliation Decision Template.
- **Completion Definition**: All lines reviewed, catalog SKUs matched, prices cross-referenced against tier schedules, arithmetic verified, discrepancies recorded, and final approval/rejection declared.

### 5.6 Error Classification
A manual reconciliation error is recorded if the participant:
1. **Missed Price Mismatch**: Fails to flag when requested unit price differs from contract tier price.
2. **Missed MOQ Violation**: Fails to flag when requested quantity is below MOQ.
3. **Missed Packaging Violation**: Fails to flag when quantity violates packaging increment.
4. **Missed Arithmetic Error**: Fails to flag discrepancy between `qty * price` and stated line total.
5. **Incorrect SKU Assignment**: Assigns an arbitrary SKU to an ambiguous description instead of flagging for customer clarification.

### 5.7 Aggregation & Reporting
- Compute median ($p50$) duration per case and total batch duration across participants.
- Compute total error count and discrepancy catch rate ($\text{caught} / \text{seeded}$).
- OrderShield evaluation runner will execute the identical cases, producing direct, audited before/after comparison tables for `docs/evaluation/manual_baseline.md`.

---

## 6. Claims Policy & Prohibited Language

To maintain strict scientific integrity and avoid ungrounded marketing hyperbole, the following claims policy is enforced across all OrderShield documentation, presentations, and README files.

### 6.1 Prohibited Assertions
The following phrases are **strictly prohibited** unless backed by committed, audited benchmark artifacts:
- `"15 minutes manually"` or `"12 minutes manually"` (must state only measured baseline median).
- `"2–4% margin loss"` or `"industry average leakage"` (must cite specific academic/industry studies or be omitted).
- `"$400 saved per order"` or arbitrary financial projections.
- `"Production accuracy"` or `"Enterprise SLA ready"` (evaluation is conducted on free-tier / synthetic test suites).
- `"Industry-wide accuracy"` or generalized claims beyond the evaluated test suite.

### 6.2 Grounded Claims Standards
- All velocity claims must reference the exact test scenario (e.g. *"Prepared 5-line demo reconciled in $X$ seconds; manual baseline required $Y$ seconds"*).
- All accuracy claims must report exact numerators and denominators (e.g. *"100% discrepancy catch rate across seeded categories in discrepancy_apex"*).
- Demo numbers must be explicitly labeled as **Prepared Demo Scenario Measurements**, not enterprise statistical generalities.

---

## 7. Evaluation Runner Interface Contract (`VLD-EVAL-02`)

The evaluation runner implemented in `VLD-EVAL-02` will execute this protocol via a unified command-line interface.

### 7.1 Command Interface
```powershell
python -m app.cli evaluate [OPTIONS]
```

### 7.2 Execution Modes
- `--mode=DETERMINISTIC`: Runs deterministic rules suite against manifest fixtures (0 network calls).
- `--mode=REPLAY`: Evaluates recorded AI completions against ground truth (0 network calls).
- `--mode=LIVE`: Executes live provider calls against configured LLM provider (`QWEN_BASE_URL` or Gemini).
- `--mode=HISTORICAL_BAKEOFF`: Validates integrity and reproduces metrics of historical bake-off logs.
- `--mode=ALL`: Executes all applicable modes based on environment configuration.

### 7.3 Output Artifacts
Every evaluation run must produce:
1. Timestamped result artifact: `docs/evaluation/results/YYYYMMDD-HHMMSS-<mode>.json`.
2. Updated latest pointer: `docs/evaluation/results/latest.json`.
3. Strict validation against `docs/evaluation/results.schema.json`.

---

## 8. Human Gate Review (`HG-EVAL-01`) Checklist

Before proceeding to runner implementation (`VLD-EVAL-02`), this protocol requires formal sign-off:

- [x] All 6 Success Criteria (SC-001..SC-006) initialized to `NOT_YET_MEASURED`.
- [x] Dedicated 5-line PO fixture authored at `docs/evaluation/fixtures/sc001_prepared_5line_po.txt`.
- [x] Canonical approval endpoint verified as `POST /api/v1/drafts/{draft_id}/approve` returning HTTP 409.
- [x] Replay mode badging verified as `is_replay_mode`.
- [x] Unextractable PDF fixture verified as HTTP 400 `UnextractableTextError`.
- [x] Historical bake-off and current application schemas strictly separated.
- [x] Historical review routing evaluated at AI boundary without catalog discrepancy requirement.
- [x] `discrepancy_apex` represented as stateful lifecycle (latent MOQ rule evaluated at State B).
- [x] Manual baseline cohort documented as small convenience sample with explicit limitations.
- [x] Invented AI acceptance thresholds removed (AI-1 and AI-2 designated REPORT ONLY).
- [x] Claims policy strictly prohibits ungrounded marketing assertions.
- [x] Implementation conformance tests explicitly separated from empirical benchmark results.
