# Observer Recording Template & Evaluation Scoring Sheet

> [!CAUTION]
> **CONFIDENTIAL OBSERVER ARTIFACT**: This document contains ground-truth answers, discrepancy keys, and scoring criteria derived from [`docs/evaluation/ground_truth_manifest.json`](../../ground_truth_manifest.json).
> **DO NOT PROVIDE OR EXPOSE THIS DOCUMENT TO STUDY PARTICIPANTS.**

---

## 1. Study Administration, Distribution Safety & Participant Eligibility

### Distribution Safety Protocol
> [!IMPORTANT]
> **STRICT PARTICIPANT ISOLATION RULE**:
> - **NEVER** give participants access to the entire repository or the `docs/evaluation/manual_study/` root directory.
> - Participant distribution packages must contain **ONLY**:
>   1. `participant_packet/participant_instructions.md`
>   2. `participant_packet/catalog_sheet.md`
>   3. `participant_packet/contract_pricing_sheet.md`
>   4. `participant_packet/reconciliation_template.md`
>   5. The single purchase order text file for the case currently being executed (Case A, then Case B, then Case C).
> - All files in `observer_only/` (this template and `study_results.schema.json`) must remain strictly confidential.

### Eligibility Standards
- **Sample Target**: 2 to 3 independent participants (e.g., `P01`, `P02`, `P03`).
- **Prior Knowledge Prohibition**: Participants must **not** have previously inspected:
  - `docs/evaluation/ground_truth_manifest.json` or answer keys;
  - Test fixtures (`po_clean_acme.txt`, `po_discrepancy_apex.txt`, `po_ambiguous_apex.txt`, `sc001_prepared_5line_po.txt`);
  - Application reconciliation code or unit tests.
- **Convenience Sample Standard**: Participants represent a small convenience sample. Do not describe participants as professional wholesale coordinators unless factually true.
- **PII Prohibition**: Record only non-identifying participant IDs (`P01`, `P02`, `P03`) and neutral background categories (e.g., `"student"`, `"developer"`, `"administrative experience"`). Do not collect or record names, emails, phone numbers, or other personally identifiable information.

### Quantitative Scope & Cognitive Burden Clarification
- **Quantitative Measurement**: Only duration (seconds) and objective reconciliation errors (error classification counts) are quantitatively measured.
- **Cognitive Burden Clarification**: While protocol §5.1 mentions operator cognitive burden, no objective burden instrument (e.g., NASA-TLX or validated survey instrument) is operationalized or defined in the protocol. To maintain scientific integrity, **cognitive burden is NOT operationalized in this study and must NOT be reported as a measured quantitative result.** No subjective scoring or invented scales are permitted.

### Participant Cohort Roster

| Participant ID | Background Category | Pre-Study Integrity Confirmation (No Prior Answer Knowledge) | Session Date | Observer Name |
| :---: | :--- | :---: | :---: | :--- |
| `P01` | | [ ] Confirmed | | |
| `P02` | | [ ] Confirmed | | |
| `P03` | | [ ] Confirmed | | |

---

## 2. Canonical Ground Truth Reference for Observer

Derived directly from `docs/evaluation/ground_truth_manifest.json`.

### Case A: Clean 5-Line PO (`docs/evaluation/fixtures/sc001_prepared_5line_po.txt`)
- **Customer**: Acme Industrial Supplies (`CUST-ACME`) | PO: `PO-ACME-5001`
- **Expected Decision**: **APPROVE**
- **Primary Scored Seeded Discrepancies**: **0**
- **Canonical Line Reconciliation**:
  - Line 1: `18in stretch film heavy duty`, Qty 10 -> `SKU-WRAP-18` (Tier Q10 = $25.00), Line Total: $250.00. [Clean]
  - Line 2: `Standard Pallet Wrap 15in 65ga`, Qty 5 -> `SKU-WRAP-15` (Tier Q5 = $20.00), Line Total: $100.00. [Clean]
  - Line 3: `Industrial Stretch Film 18in 80ga`, Qty 10 -> `SKU-WRAP-18` (Tier Q10 = $25.00), Line Total: $250.00. [Clean]
  - Line 4: `Standard Pallet Wrap 15in 65ga`, Qty 25 -> `SKU-WRAP-15` (Tier Q25 = $18.50), Line Total: $462.50. [Clean]
  - Line 5: `Industrial Stretch Film 18in 80ga`, Qty 50 -> `SKU-WRAP-18` (Tier Q50 = $23.00), Line Total: $1150.00. [Clean]
  - Subtotal / Total: $2,212.50.

### Case B: Discrepancy PO (`tests/fixtures/po_discrepancy_apex.txt`)
- **Customer**: Apex Distribution (`CUST-APEX`) | PO: `PO-APEX-7741`
- **Expected Decision**: **REJECT / NEEDS REVIEW**
- **Primary Scored Seeded Discrepancies**: **2**
  1. `PriceMismatch` (Line 1): Requested unit price $18.00 vs contract tier price $22.00 (Apex tier for `SKU-WRAP-18` at Q=10 is $22.00).
  2. `Catalog ambiguity / Needs clarification` (Line 2): Customer description `"Standard pallet wrap"` matches multiple catalog products (`SKU-WRAP-15` vs `SKU-WRAP-18`). Must be routed for clarification, not guessed.
- **Documented Secondary Stateful Observation (Latent MOQ)**:
  - Line 2 requested quantity is 2. The catalog MOQ for `SKU-WRAP-15` is 5 (and for `SKU-WRAP-18` is also 5).
  - In accordance with accepted stateful domain semantics (ADR 0001 and manifest stateful lifecycle), this packaging/MOQ rule is **latent** until SKU resolution.
  - Because participant instructions correctly require marking an ambiguous SKU as "Needs clarification" rather than guessing, a participant who marks "Needs clarification" has executed the correct manual workflow.
  - **Scoring Invariant**: Do **NOT** count latent MOQ as a participant miss when the participant correctly refuses to choose an ambiguous SKU.
  - Do **NOT** include latent MOQ in the primary manual catch-rate denominator.
  - Record whether the participant spontaneously noted the MOQ issue as a documentary observation only.

### Case C: Ambiguous PO (`tests/fixtures/po_ambiguous_apex.txt`)
- **Customer**: Apex Distribution (`CUST-APEX`) | PO: `PO-APEX-8820`
- **Expected Decision**: **REJECT / NEEDS REVIEW**
- **Primary Scored Seeded Discrepancies**: **1**
  1. `Catalog ambiguity / Needs clarification` (Line 1): Description `"Standard pallet wrap"` matches multiple catalog products (`SKU-WRAP-15` vs `SKU-WRAP-18`).
- **Trap / Error Condition**: Participant guessing or arbitrarily selecting `SKU-WRAP-15` or `SKU-WRAP-18` instead of marking "Needs clarification" counts as an **Incorrect SKU assignment**.

---

## 3. Approved Error Classification

Score errors strictly against the following 5 objective categories (no subjective scoring):

1. **Missed Price Mismatch**: Participant fails to identify when requested unit price differs from the contract tier price.
2. **Missed MOQ Violation**: Participant fails to identify when requested quantity is below the catalog Minimum Order Quantity on a determinate catalog SKU.
3. **Missed Package Violation**: Participant fails to identify when requested quantity violates the catalog packaging increment.
4. **Missed Arithmetic Error**: Participant fails to identify discrepancy between `quantity * price` and stated line/order total.
5. **Incorrect SKU Assignment**: Participant assigns an arbitrary SKU to an ambiguous description instead of flagging for customer clarification.

---

## 4. Participant Session Recording Sheets

Record individual measurements for each participant and case.

### Participant P01

#### P01 — Case A (`sc001_prepared_5line_po.txt`)
- **Start Timestamp (UTC)**:
- **Stop Timestamp (UTC)**:
- **Duration (seconds)**:
- **Participant Decision**: [ ] APPROVE  [ ] REJECT / NEEDS REVIEW
- **Expected Decision**: APPROVE
- **Primary Seeded Discrepancies**: 0
- **Correctly Identified Discrepancies**:
- **Missed Discrepancies Breakdown**:
  - Missed Price Mismatch: 0
  - Missed MOQ Violation: 0
  - Missed Package Violation: 0
  - Missed Arithmetic Error: 0
  - Missed Ambiguous SKU Review: 0
- **Incorrect SKU Assignment Count**: 0
- **False Positive Flags (Flagged non-existent error)**:
- **Procedural Deviations / Notes**:

#### P01 — Case B (`po_discrepancy_apex.txt`)
- **Start Timestamp (UTC)**:
- **Stop Timestamp (UTC)**:
- **Duration (seconds)**:
- **Participant Decision**: [ ] APPROVE  [ ] REJECT / NEEDS REVIEW
- **Expected Decision**: REJECT / NEEDS REVIEW
- **Primary Seeded Discrepancies**: 2 (Line 1 PriceMismatch, Line 2 Ambiguity)
- **Correctly Identified Discrepancies (0 to 2)**:
- **Missed Discrepancies Breakdown (Primary Scored)**:
  - Missed Price Mismatch (Line 1):
  - Missed Ambiguous SKU Review (Line 2):
  - Missed Package Violation: 0
  - Missed Arithmetic Error: 0
- **Incorrect SKU Assignment Count**:
- **Secondary Stateful Observation**:
  - Participant noted latent Line 2 MOQ breach? [ ] Yes  [ ] No  *(Documentary observation only; not scored as a miss)*
- **Procedural Deviations / Notes**:

#### P01 — Case C (`po_ambiguous_apex.txt`)
- **Start Timestamp (UTC)**:
- **Stop Timestamp (UTC)**:
- **Duration (seconds)**:
- **Participant Decision**: [ ] APPROVE  [ ] REJECT / NEEDS REVIEW
- **Expected Decision**: REJECT / NEEDS REVIEW
- **Primary Seeded Discrepancies**: 1 (Line 1 Ambiguity)
- **Correctly Identified Discrepancies (0 to 1)**:
- **Missed Discrepancies Breakdown**:
  - Missed Ambiguous SKU Review (Line 1):
  - Missed Price Mismatch: 0
  - Missed MOQ Violation: 0
  - Missed Package Violation: 0
  - Missed Arithmetic Error: 0
- **Incorrect SKU Assignment Count**:
- **Procedural Deviations / Notes**:

---

### Participant P02

#### P02 — Case A (`sc001_prepared_5line_po.txt`)
- **Start Timestamp (UTC)**:
- **Stop Timestamp (UTC)**:
- **Duration (seconds)**:
- **Participant Decision**: [ ] APPROVE  [ ] REJECT / NEEDS REVIEW
- **Expected Decision**: APPROVE
- **Primary Seeded Discrepancies**: 0
- **Correctly Identified Discrepancies**:
- **Missed Discrepancies Breakdown**:
  - Missed Price Mismatch: 0
  - Missed MOQ Violation: 0
  - Missed Package Violation: 0
  - Missed Arithmetic Error: 0
  - Missed Ambiguous SKU Review: 0
- **Incorrect SKU Assignment Count**: 0
- **False Positive Flags**:
- **Procedural Deviations / Notes**:

#### P02 — Case B (`po_discrepancy_apex.txt`)
- **Start Timestamp (UTC)**:
- **Stop Timestamp (UTC)**:
- **Duration (seconds)**:
- **Participant Decision**: [ ] APPROVE  [ ] REJECT / NEEDS REVIEW
- **Expected Decision**: REJECT / NEEDS REVIEW
- **Primary Seeded Discrepancies**: 2 (Line 1 PriceMismatch, Line 2 Ambiguity)
- **Correctly Identified Discrepancies (0 to 2)**:
- **Missed Discrepancies Breakdown (Primary Scored)**:
  - Missed Price Mismatch (Line 1):
  - Missed Ambiguous SKU Review (Line 2):
  - Missed Package Violation: 0
  - Missed Arithmetic Error: 0
- **Incorrect SKU Assignment Count**:
- **Secondary Stateful Observation**:
  - Participant noted latent Line 2 MOQ breach? [ ] Yes  [ ] No  *(Documentary observation only; not scored as a miss)*
- **Procedural Deviations / Notes**:

#### P02 — Case C (`po_ambiguous_apex.txt`)
- **Start Timestamp (UTC)**:
- **Stop Timestamp (UTC)**:
- **Duration (seconds)**:
- **Participant Decision**: [ ] APPROVE  [ ] REJECT / NEEDS REVIEW
- **Expected Decision**: REJECT / NEEDS REVIEW
- **Primary Seeded Discrepancies**: 1 (Line 1 Ambiguity)
- **Correctly Identified Discrepancies (0 to 1)**:
- **Missed Discrepancies Breakdown**:
  - Missed Ambiguous SKU Review (Line 1):
  - Missed Price Mismatch: 0
  - Missed MOQ Violation: 0
  - Missed Package Violation: 0
  - Missed Arithmetic Error: 0
- **Incorrect SKU Assignment Count**:
- **Procedural Deviations / Notes**:

---

### Participant P03 (Optional / 3rd Participant)

#### P03 — Case A (`sc001_prepared_5line_po.txt`)
- **Start Timestamp (UTC)**:
- **Stop Timestamp (UTC)**:
- **Duration (seconds)**:
- **Participant Decision**: [ ] APPROVE  [ ] REJECT / NEEDS REVIEW
- **Expected Decision**: APPROVE
- **Primary Seeded Discrepancies**: 0
- **Correctly Identified Discrepancies**:
- **Missed Discrepancies Breakdown**:
  - Missed Price Mismatch: 0
  - Missed MOQ Violation: 0
  - Missed Package Violation: 0
  - Missed Arithmetic Error: 0
  - Missed Ambiguous SKU Review: 0
- **Incorrect SKU Assignment Count**: 0
- **False Positive Flags**:
- **Procedural Deviations / Notes**:

#### P03 — Case B (`po_discrepancy_apex.txt`)
- **Start Timestamp (UTC)**:
- **Stop Timestamp (UTC)**:
- **Duration (seconds)**:
- **Participant Decision**: [ ] APPROVE  [ ] REJECT / NEEDS REVIEW
- **Expected Decision**: REJECT / NEEDS REVIEW
- **Primary Seeded Discrepancies**: 2 (Line 1 PriceMismatch, Line 2 Ambiguity)
- **Correctly Identified Discrepancies (0 to 2)**:
- **Missed Discrepancies Breakdown (Primary Scored)**:
  - Missed Price Mismatch (Line 1):
  - Missed Ambiguous SKU Review (Line 2):
  - Missed Package Violation: 0
  - Missed Arithmetic Error: 0
- **Incorrect SKU Assignment Count**:
- **Secondary Stateful Observation**:
  - Participant noted latent Line 2 MOQ breach? [ ] Yes  [ ] No  *(Documentary observation only; not scored as a miss)*
- **Procedural Deviations / Notes**:

#### P03 — Case C (`po_ambiguous_apex.txt`)
- **Start Timestamp (UTC)**:
- **Stop Timestamp (UTC)**:
- **Duration (seconds)**:
- **Participant Decision**: [ ] APPROVE  [ ] REJECT / NEEDS REVIEW
- **Expected Decision**: REJECT / NEEDS REVIEW
- **Primary Seeded Discrepancies**: 1 (Line 1 Ambiguity)
- **Correctly Identified Discrepancies (0 to 1)**:
- **Missed Discrepancies Breakdown**:
  - Missed Ambiguous SKU Review (Line 1):
  - Missed Price Mismatch: 0
  - Missed MOQ Violation: 0
  - Missed Package Violation: 0
  - Missed Arithmetic Error: 0
- **Incorrect SKU Assignment Count**:
- **Procedural Deviations / Notes**:

---

## 5. Deterministic Aggregation Plan

All aggregate figures must be computed using deterministic formulas prior to publishing results.

### Per-Case Aggregations

1. **Durations**:
   - Collect individual durations: $D = [d_1, d_2, \dots, d_N]$.
   - Compute Median ($p50$):
     - For $N=3$: middle sorted value.
     - For $N=2$: arithmetic mean of the two values.
2. **Error Counts**:
   - Sum of all errors across participants for that case:
     $$\text{Total Errors} = \sum_{i=1}^N (\text{Missed Errors}_i + \text{Incorrect SKU Assignments}_i)$$
3. **Discrepancy Catch Rate**:
   - Total Seeded Discrepancies across cohort: $N \times \text{Primary Seeded Discrepancies per case}$.
   - Total Caught Discrepancies: $\sum_{i=1}^N \text{Correctly Identified Discrepancies}_i$.
   - Catch Rate:
     $$\text{Catch Rate} = \frac{\sum_{i=1}^N \text{Correctly Identified}_i}{N \times \text{Primary Seeded Discrepancies}} \times 100\%$$
   *(For Case A where Seeded = 0, catch rate is N/A; record false positive count instead).*

### Per-Case Summary Table (To be populated after study execution)

| Case ID | Fixture Description | Sample Size ($N$) | Participant Durations (s) | Median Duration ($p50$ s) | Total Errors | Primary Seeded ($N \times \text{seeded}$) | Caught Discrepancies | Catch Rate (%) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Case A** | Clean 5-line PO (`sc001_prepared_5line`) | | `[ ]` | | | 0 | 0 | N/A |
| **Case B** | Discrepancy PO (`discrepancy_apex`) | | `[ ]` | | | $2N$ | | |
| **Case C** | Ambiguous PO (`ambiguous_apex`) | | `[ ]` | | | $1N$ | | |

### Overall Manual Batch Aggregations

- **Total Duration per Participant**: $T_i = d_{A,i} + d_{B,i} + d_{C,i}$
- **Cohort Total Durations**: $[T_1, T_2, \dots, T_N]$
- **Median Total Batch Duration**: $\text{median}([T_1, T_2, \dots, T_N])$
- **Primary Total Seeded Across Batch**:
  $$N \times (0 + 2 + 1) = 3N$$
  *(For $N=2 \implies 6$; for $N=3 \implies 9$)*
- **Total Caught Across Batch**: $\sum_{i=1}^N (\text{Caught}_{A,i} + \text{Caught}_{B,i} + \text{Caught}_{C,i})$
- **Batch Discrepancy Catch Rate**:
  $$\text{Batch Catch Rate} = \frac{\text{Total Caught}}{3N} \times 100\%$$
- **Sample Size Statement**: Must explicitly report $N$ (e.g., $N=2$ or $N=3$).

### Reporting Integrity Invariants
- **No Extrapolation**: Do not claim these figures represent industry-wide reconciliation speed or commercial baselines.
- **No Invented Cost Savings**: Do not assert arbitrary dollar savings or ROI estimates.
- **No Statistical Significance**: Acknowledge that $N=2\text{--}3$ is a convenience baseline benchmark.
- **No Quantitative Cognitive Burden Claims**: Cognitive burden is not operationalized and must not be asserted as a measured quantitative metric.

---

## 6. SC-001 Assisted OrderShield Run

This section defines the assisted operator timing protocol for evaluating Success Criterion SC-001 under OrderShield.

### Protocol & Timer Semantics
- **Canonical Fixture**: `docs/evaluation/fixtures/sc001_prepared_5line_po.txt`
- **Timer Start**: Operator initiates submission of the prepared 5-line PO in the OrderShield UI.
- **Timer Stop**: Final approval is completed and the `Verified Order` result is visibly returned on the screen.
- **SC-001 Criterion**:
  - **PASS**: Actual assisted operator completion duration $< 60.0$ seconds.
  - **FAIL**: Actual assisted operator completion duration $\ge 60.0$ seconds.

### Assisted Run Recording Table

| Metric | Recorded Value | Evaluation Standard | Status |
| :--- | :--- | :--- | :--- |
| **Operator ID** | *(Leave blank during EVAL-03A)* | Dedicated operator | Pending execution |
| **Canonical Intake Fixture** | `docs/evaluation/fixtures/sc001_prepared_5line_po.txt` | Approved 5-line fixture | Verified |
| **Assisted Operator Start Timestamp (UTC)** | *(Leave blank during EVAL-03A)* | Order submission click | Pending execution |
| **Assisted Operator Stop Timestamp (UTC)** | *(Leave blank during EVAL-03A)* | Verified Order visible | Pending execution |
| **`assisted_operator_completion_duration_seconds`** | *(Leave blank during EVAL-03A)* | $< 60.0\text{ s}$ for PASS | **`NOT_YET_MEASURED`** |
| **SC-001 Status** | **`PARTIALLY_MEASURED`** | Requires assisted run | In Progress |

> [!IMPORTANT]
> **Intake Path Decision Requirement**: The current application has no accepted replay extraction asset for this canonical 5-line PO (`sc001_prepared_5line_po.txt`). Therefore, do **NOT** silently substitute `clean_acme` and do **NOT** fabricate system timing. Actual assisted execution requires an explicit project decision regarding the 5-line intake path.

---

## 7. Study Design Warning: Operator Contamination & Familiarity

- **Primary Risk**: If the same person who solved Case A manually later acts as the assisted OrderShield operator, their familiarity with line items and correct prices may artificially deflate assisted interaction duration.
- **Mitigation / Preferred Cohort Separation**:
  - **Manual Baseline Participants**: 2–3 individuals who have no prior knowledge of the test cases or solutions.
  - **Assisted SC-001 Operator**: A separate individual who has not manually reconciled Case A beforehand.
- **Contamination Disclosure Requirement**: If resource constraints require using an operator who previously inspected or reconciled Case A, this limitation must be explicitly recorded below:

```
[ Operator Contamination / Familiarity Disclosure: Record any crossover between manual baseline participants and assisted operator here ]
```
