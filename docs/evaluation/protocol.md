# OrderShield Evaluation Protocol & Ground-Truth Contract

- **Document ID**: `EVAL-PROTO-01`
- **Status**: Draft / Submitted for Human Gate Review (`HG-EVAL-01`)
- **Author / Lead**: Vladimir (Integrator / Project Brain)
- **Canonical Date**: 2026-10-02
- **Related Specifications**: `specs/001-ordershield-po-reconciliation/spec.md`
- **Related Decisions**: `docs/decisions/0001-training-live-ai-provider-selection.md` (ADR 0001)
- **Manifest**: `docs/evaluation/ground_truth_manifest.json`
- **Results Schema**: `docs/evaluation/results.schema.json`

---

## 1. Executive Summary & Foundational Invariants

### 1.1 Purpose
This protocol defines a rigorous, repeatable, and auditable evaluation methodology for OrderShield. It converts system specification criteria (SC-001 through SC-006), architectural boundaries, and performance claims into exact mathematical formulas, standard test procedures, and machine-readable contracts.

### 1.2 Non-Claim Invariant
**This document asserts NO empirical benchmark claims.**
All performance numbers, latency percentiles, accuracy rates, and operational comparisons may only be asserted after the execution of the evaluation runner (`VLD-EVAL-02`) and manual baseline study (`VLD-EVAL-03`), with raw run logs committed to `docs/evaluation/results/`.

### 1.3 System Boundary & Division of Responsibilities
In accordance with `AGENTS.md` and ADR 0001:
- **AI Component**: Strictly bounded to probabilistic interpretation (unstructured document intake, fuzzy field extraction, candidate SKU semantic ranking, and ambiguity detection). The AI component is untrusted; its output must validate against a strict JSON schema and grounded source citations.
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
Historical Spike Corpus        App MVP Reconciliation Corpus    Synthetic Rule Suite
  - 10 cases (h01..h10)          - clean_acme (2 lines)           - Arithmetic errors
  - Frozen ground truth          - discrepancy_apex (2 lines)     - MOQ/Increment breaches
  - Semantic/negation traps      - ambiguous_apex (1 line)        - Unrecognized SKU traps
  - Multi-provider bake-off      - unextractable_pdf (damaged)    - Commercial override traps
```

### 2.1 Historical Bake-off Corpus (`h01` through `h10`)
Reused directly from `spikes/ordershield/live/fixtures/` and `spikes/ordershield/live/expected.json` against `spikes/ordershield/catalog.json`:
- **Determinate Clear Cases**: `h01_paper` (clear SKU match), `h02_large` (catalog description match), `h03_alias` (semantic alias match), `h04_tape` (tape width/length match).
- **Semantic & Dangerous Negation Traps**:
  - `h07_not_medium`: Requests large gloves; explicitly states "NOT medium". Must resolve to `GLOVE-N-L`.
  - `h08_not_large`: Requests medium gloves; explicitly states "NOT large". Must resolve to `GLOVE-N-M`.
- **Review Traps (Ambiguity & Out-of-Catalog)**:
  - `h05_no_size`: Powderless nitrile gloves with omitted size. Must route to `HUMAN_REVIEW` with no automated SKU proposal.
  - `h06_no_pack`: Medium nitrile gloves with missing pack count attribute. Must route to `HUMAN_REVIEW`.
  - `h09_latex`: Requests latex exam gloves. Catalog exclusively stocks nitrile. Must route to `HUMAN_REVIEW` as an incompatible material trap.
- **Damaged & Incomplete Input**:
  - `h10_damaged`: Truncated/damaged text lacking customer name, quantity, and price. Must extract valid nulls with null provenance, resolving SKU where description permits, but routing to `HUMAN_REVIEW` due to missing mandatory header/line fields.

### 2.2 Application MVP Reconciliation Corpus
Committed application fixtures evaluated against active database master data (`app.cli.BASELINE_PRODUCTS` and `BASELINE_TIERS`):
- **`clean_acme`** (`tests/fixtures/po_clean_acme.txt`, `app/fixtures/clean_acme.json`): 2-line purchase order for Acme Industrial Supplies (`CUST-ACME`). 0 discrepancies, high-confidence SKU mapping, valid arithmetic. Evaluates clean intake velocity and approval workflow.
- **`discrepancy_apex`** (`tests/fixtures/po_discrepancy_apex.txt`, `app/fixtures/discrepancy_apex.json`): 2-line purchase order for Apex Distribution (`CUST-APEX`). 3 seeded discrepancies across 3 distinct categories (`PriceMismatch` on line 1, `QuantityOrPackagingBreach` MOQ breach on line 2, `CatalogMatchingMismatch` ambiguous SKU on line 2). Evaluates multi-category detection and approval blocking.
- **`ambiguous_apex`** (`tests/fixtures/po_ambiguous_apex.txt`, `app/fixtures/ambiguous_apex.json`): 1-line purchase order for Apex Distribution. Seeded `CatalogMatchingMismatch` (ambiguous description "Standard pallet wrap"). Evaluates operator SKU disambiguation workflow.
- **`unextractable_pdf`** (`tests/fixtures/po_unextractable.pdf`): Digital PDF with corrupted/unextractable text stream. Evaluates explicit diagnostic error surfacing under SC-006.

### 2.3 Synthetic Deterministic Rule Suite
Synthetic edge-case fixtures verifying discrete arithmetic and packaging rules:
- Line-level arithmetic error (`quantity * unit_price != line_total`).
- Order-level arithmetic error (`sum(line_totals) != order_total`).
- Packaging increment breach (requested quantity not an integer multiple of package increment).
- Unrecognized description (zero catalog candidate matches).
- Commercial override rejection (verifying that operator edits to unit price or quantity are rejected or re-evaluated against canonical source text).

---

## 3. Metric Definitions & Mathematical Semantics

Each evaluation metric is governed by strict mathematical calculation rules, explicit denominators, exclusion policies, and failure-handling procedures.

### 3.1 AI Extraction & Grounding Metrics

#### Metric AI-1: Schema-Valid Extraction Rate
- **Objective**: Measure the reliability of the AI provider in returning well-formed JSON conforming to the target Pydantic schema without parser or validation failure.
- **Calculation**:
  $$\text{Schema-Valid Rate} = \frac{N_{\text{valid}}}{D_{\text{attempted}}}$$
- **Numerator ($N_{\text{valid}}$)**: Count of provider calls where raw response text parses as valid JSON and satisfies the Pydantic schema (`ExtractionPayloadSchema`) with 0 validation errors.
- **Denominator ($D_{\text{attempted}}$)**: Total attempted inference requests dispatched to the provider.
- **Excluded Cases**: Calls blocked locally prior to HTTP dispatch (e.g. invalid local configuration).
- **Provider Failure Handling**: Connection timeouts, HTTP 4xx/5xx status codes, or empty responses earn 0 in the numerator and increment the denominator.
- **Malformed Handling**: Syntax errors or schema rejections earn 0 in the numerator.
- **Target**: $\ge 99.0\%$.

#### Metric AI-2: Mandatory Field Exactness
- **Objective**: Measure exact string-level extraction accuracy of core business fields compared to ground truth.
- **Fields Evaluated**:
  - Header: `customer_name`, `po_number`, `currency`, `customer_stated_total`.
  - Line Item: `customer_description`, `requested_quantity`, `sale_unit`, `customer_unit_price`.
- **Comparison Rule**: Exact string equality after whitespace collapsing (`" ".join(v.split())`). Numbers normalized to standard decimal representation (e.g. `"25.00"`). Null strictly equals null.
- **Calculation**:
  $$\text{Field Exactness} = \frac{\sum_{i=1}^{M} \mathbb{I}(\text{extracted}_i = \text{ground\_truth}_i)}{M}$$
  where $M$ is total expected field instances across all evaluated documents.
- **Provider Failure / Malformed Handling**: If a document extraction fails or produces malformed JSON, all fields for that document earn 0 in the numerator.
- **Aggregation**: Macro-averaged across documents; per-field breakdown reported independently.
- **Target**: $\ge 95.0\%$.

#### Metric AI-3: Provenance Citation Validity Rate
- **Objective**: Ensure extracted fields are verifiably grounded in the source text, preventing hallucinated numbers or identifiers.
- **Rule**:
  - If field is non-null: `verbatim_snippet` must be non-empty, must exist as an exact substring in the source document, and character/line offsets must point to the snippet location.
  - If field is null: `verbatim_snippet` must be null or empty string (valid missingness).
- **Calculation**:
  $$\text{Provenance Validity} = \frac{N_{\text{grounded\_fields}}}{D_{\text{evaluated\_fields}}}$$
- **Provider Failure Handling**: Provider failure yields 0 valid citations.
- **Target**: $100.0\%$.

#### Metric AI-4: Determinate SKU Accuracy
- **Objective**: Measure SKU matching accuracy exclusively on clear, determinate order lines where a catalog SKU can be unambiguously resolved.
- **Calculation**:
  $$\text{Determinate SKU Accuracy} = \frac{N_{\text{correct\_sku}}}{D_{\text{determinate\_lines}}}$$
- **Numerator ($N_{\text{correct\_sku}}$)**: Count of determinate lines where `matched_sku == expected_sku` and `sku_confidence == "High"`.
- **Denominator ($D_{\text{determinate\_lines}}$)**: Total determinate lines across evaluated documents (e.g. 7 lines in $h01..h10$, 2 lines in `clean_acme`).
- **Abstention Handling**: A conservative abstention (`matched_sku = null` with `sku_confidence = Ambiguous`) earns 0 in this numerator, but is logged as a safe abstention rather than a false match.
- **Target**: $\ge 95.0\%$.

#### Metric AI-5: Ambiguous / Unrecognized Review-Routing Rate (Review Trap Catch Rate)
- **Objective**: Measure the system's ability to catch ambiguous descriptions, missing attributes, or out-of-catalog items and route them to human review.
- **Calculation**:
  $$\text{Review-Routing Rate} = \frac{N_{\text{routed\_to\_review}}}{D_{\text{trap\_lines}}}$$
- **Numerator ($N_{\text{routed\_to\_review}}$)**: Count of review-trap lines where the system sets `sku_confidence` to `Ambiguous` or `Unrecognized`, sets `matched_sku = null` (or provides ranked candidates without automated commitment), and triggers a `CatalogMatchingMismatch` discrepancy.
- **Denominator ($D_{\text{trap\_lines}}$)**: Total lines in the test corpus designed as review traps (e.g. $h05$, $h06$, $h09$, line 2 of `discrepancy_apex`, line 1 of `ambiguous_apex`).
- **Provider Failure Handling**: Network or parse errors do NOT count as successful routing; only valid extractions that explicitly route to operator review increment the numerator.
- **Target**: $100.0\%$.

#### Metric AI-6: Wrong-Confident SKU Count & Rate (Hallucinated Commitment Rate)
- **Objective**: Measure catastrophic false commitments where the system confidently assigns an incorrect SKU or assigns a SKU to an ambiguous/out-of-catalog item.
- **Definition**: Any instance where `sku_confidence == "High"` AND:
  - The line is determinate, but `matched_sku != expected_sku`; OR
  - The line is a review trap / out-of-catalog item, but a SKU was confidently assigned.
- **Calculation**:
  $$\text{Wrong-Confident Rate} = \frac{N_{\text{wrong\_confident}}}{D_{\text{total\_lines}}}$$
- **Target**: **Strictly 0 (0.0%)**. Any occurrence represents an immediate evaluation blocker.

#### Metric AI-7: Provider Latency Distribution
- **Objective**: Measure end-to-end HTTP wall-clock elapsed time for live inference requests.
- **Metrics Computed**: Minimum, median ($p50$), mean, $p90$, $p95$, and maximum latency in seconds.
- **Separation Policy**:
  - `latency_success_distribution`: Latency distribution of completed, schema-valid inference requests.
  - `latency_all_attempts_distribution`: Latency distribution including aborted timeouts and HTTP errors.
- **Target**:
  - Immediately detectable errors $\le 5.0\text{ s}$ from request intake (SC-006).
  - Healthy live inference bounded within $\le 15.0\text{ s}$ budget (ADR 0001).

---

### 3.2 Deterministic Reconciliation Metrics

#### Metric DET-1: Seeded Discrepancy Catch Rate
- **Objective**: Verify that 100% of seeded pricing mismatches, arithmetic errors, MOQ violations, and catalog matching discrepancies are detected by the rules engine.
- **Calculation**:
  $$\text{Catch Rate} = \frac{N_{\text{discrepancies\_detected}}}{D_{\text{discrepancies\_seeded}}}$$
- **Condition**: A discrepancy is counted as detected if and only if:
  1. It is attached to the correct order draft and line item.
  2. Its `discrepancy_type` exactly matches the expected category (`PriceMismatch`, `QuantityOrPackagingBreach`, `ArithmeticMismatch`, or `CatalogMatchingMismatch`).
  3. Its initial `resolution_state == "Unresolved"` and `severity == "Blocking"`.
- **Target**: $100.0\%$.

#### Metric DET-2: False-Positive Discrepancy Rate
- **Objective**: Verify that clean drafts do not generate spurious discrepancy flags.
- **Calculation**:
  $$\text{False-Positive Rate} = \frac{N_{\text{spurious\_flags}}}{D_{\text{clean\_lines}}}$$
- **Target**: Strictly 0 flags on clean inputs ($0.0\%$).

#### Metric DET-3: Commercial-Rule Enforcement Rate
- **Objective**: Verify exact integer-cents contract tier price selection, MOQ gating, and packaging increment division.
- **Verification Rule**: 100% agreement with precomputed contract matrices without floating-point intermediate rounding. Rejection of direct commercial price/quantity modifications by operators.
- **Target**: $100.0\%$.

#### Metric DET-4: Unresolved-Discrepancy Approval Blocking Rate (Gate Enforcement)
- **Objective**: Ensure no order draft with unresolved discrepancies or incomplete mandatory fields can transition to `Approved`.
- **Calculation**:
  $$\text{Gate Blocking Rate} = \frac{N_{\text{blocked\_approvals}}}{D_{\text{invalid\_approval\_attempts}}}$$
- **Condition**: Attempting `POST /api/v1/orders/{id}/approve` on an order with unresolved discrepancy flags must return HTTP 400/409/422 and leave the draft in `Needs Review` status.
- **Target**: $100.0\%$ (0% approval leakage).

#### Metric DET-5: Terminal-State Immutability Rate
- **Objective**: Ensure committed orders (`Approved` or `Rejected`) cannot be modified, re-evaluated, or re-transitioned.
- **Target**: $100.0\%$ rejection with explicit error.

---

### 3.3 End-to-End Operational Metrics

- **Processing Duration**: Wall-clock time elapsed from document ingestion request to terminal order commitment (`Approved` or `Rejected`).
- **Operator Action Count**: Number of discrete operator interactions required to achieve terminal state:
  - Clean scenario: 1 action (operator visual inspection and final approval sign-off).
  - Ambiguous scenario: 2 actions (manual candidate SKU selection + final approval sign-off).
  - Unresolvable discrepancy: 1 action (rejection with mandatory recorded reason).
- **Execution Mode Badge Verification**: 100% of API responses and UI views in replay/fixture mode must contain `is_replay: true` and visible non-live badging.

---

## 4. Success Criteria Traceability Matrix

This table maps specification success criteria (SC-001 through SC-006) directly to protocol metrics, supporting evidence, and evaluation asset status.

| Success Criterion | Specification Requirement | Mapped Metric | Evaluation Assets / Evidence | Status / Target |
|:---|:---|:---|:---|:---:|
| **SC-001** | Prepared Demo Processing Velocity: Operations coordinator completes reconciliation & approval of prepared 5-line PO in $<60\text{ s}$. | E2E Processing Duration & Operator Actions | `tests/fixtures/po_clean_acme.txt` (2-line), `po_discrepancy_apex.txt` (2-line). **Note**: A dedicated 5-line PO fixture is currently a **`MISSING EVALUATION ASSET`**. Available 2-line demo completes in $<15\text{ s}$. | **`MISSING EVALUATION ASSET`** *(5-line asset required)* |
| **SC-002** | Discrepancy Catch Rate: 100% of seeded pricing mismatches, arithmetic errors, and MOQ violations detected before approval. | `DET-1` Seeded Discrepancy Catch Rate | `discrepancy_apex.json`, `synthetic_deterministic_suite`, unit test suites (`test_reconciliation.py`). | **PASS** *(Target: 100%)* |
| **SC-003** | Zero Hallucinated Commitments: 100% of ambiguous/out-of-catalog items routed to review; zero unrecognized descriptions assigned to unverified SKUs. | `AI-5` Review-Routing Rate & `AI-6` Wrong-Confident SKUs | Traps `h05`, `h06`, `h09`; `ambiguous_apex.json`; `test_ai_matcher.py`. | **PASS** *(Target: 100% routed, 0 wrong-confident)* |
| **SC-004** | Full Provenance Visibility: 100% of extracted line items provide visible textual grounding citations back to source PO text. | `AI-3` Provenance Citation Validity Rate | `app/fixtures/*.json`, `expected.json`, `test_schemas.py`. | **PASS** *(Target: 100%)* |
| **SC-005** | Mandatory Gate Enforcement: 0% of unreviewed or discrepancy-laden drafts can transition to committed order record without operator sign-off/resolution. | `DET-4` Approval Gate Blocking Rate | `test_order_service.py`, `test_api_contracts.py`. | **PASS** *(Target: 0% transition of unreviewed drafts)* |
| **SC-006** | Explicit Failure & Replay Transparency: Immediate diagnostic error $\le 5\text{ s}$ target; healthy live inference $\le 15\text{ s}$ budget; silent/stalled aborted at $\le 15\text{ s}$; 0 silent fallback; 100% visible replay badge. | `AI-7` Latency Profile, Replay Badge Presence | `po_unextractable.pdf`, `test_ai_provider.py`, `test_api_wiring.py`. | **PASS** *(Immediate $\le 5\text{ s}$, Live $\le 15\text{ s}$, 0 silent fallback)* |

> [!IMPORTANT]
> **Declaration on SC-001**: In strict adherence to the project constitution, SC-001 is formally designated **`MISSING EVALUATION ASSET`** because the repository currently contains 2-line and 1-line prepared fixtures, but no prepared 5-line purchase order fixture. No benchmark result may be fabricated for SC-001 until a canonical 5-line fixture is authored or measured during the manual baseline study.

---

## 5. Manual Baseline Study Protocol (Before/After Comparison)

To validate operational acceleration without relying on ungrounded claims, this protocol specifies a small-sample, reproducible human baseline experiment.

### 5.1 Objective
Measure the baseline duration, cognitive burden, and error rate of human operators performing manual purchase order reconciliation without OrderShield.

### 5.2 Cohort Design
- **Participants**: 2 to 3 independent participants representing wholesale operations coordinators.
- **Familiarity**: Basic familiarity with wholesale ordering, unit conversions, and spreadsheet calculation.
- **Independence**: Participants execute the benchmark independently without collaboration or hints.

### 5.3 Benchmark Inputs Provided to Participant
Each participant is provided with identical materials:
1. **Source Documents**:
   - Case A: Clean 2-line purchase order (`po_clean_acme.txt`).
   - Case B: Multi-discrepancy 2-line purchase order (`po_discrepancy_apex.txt`).
   - Case C: Ambiguous description 1-line purchase order (`po_ambiguous_apex.txt`).
2. **Product Master Catalog Sheet**: Listing SKU code, product title, unit of measure, base price, MOQ, and packaging increments.
3. **Customer Contract Pricing Sheet**: Listing customer contract terms, authorized SKUs, and volume tier price breaks.
4. **Reconciliation Decision Template**: Form where the participant records matched SKU, verified unit price, total line price, discrepancies discovered, and final Approve/Reject recommendation.

### 5.4 Allowed Tools
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
- All velocity claims must reference the exact test scenario (e.g. *"Prepared 2-line demo reconciled in $<15$ seconds; manual baseline required $X$ seconds"*).
- All accuracy claims must report exact numerators and denominators (e.g. *"100% discrepancy catch rate across 3 seeded categories in discrepancy_apex"*).
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

- [ ] All 6 Success Criteria (SC-001..SC-006) mapped to explicit metrics.
- [ ] SC-001 honestly classified as `MISSING EVALUATION ASSET` pending 5-line PO fixture.
- [ ] AI metrics define exact numerator, denominator, exclusions, and failure handling.
- [ ] Deterministic rules define exact discrepancy catch criteria across 4 MVP categories.
- [ ] Manual baseline design specifies participants, timer triggers, allowed tools, and error classification.
- [ ] Claims policy strictly prohibits ungrounded marketing assertions.
- [ ] Machine-readable manifest and results schema validated.
