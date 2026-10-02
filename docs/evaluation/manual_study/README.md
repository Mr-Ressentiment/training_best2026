# Manual Baseline Study Packet & Measurement Preparation (VLD-EVAL-03A)

- **Status**: **MANUAL BASELINE EXECUTION AUTHORIZED**
- **Human Gates**:
  - `HG-EVAL-01` = **APPROVED**
  - `HG-EVAL-03A` = **APPROVED**
  - `HG-EVAL-03A-RU` = **APPROVED**
- **Protocol Reference**: [`docs/evaluation/protocol.md`](../protocol.md) (§5)
- **Ground Truth Manifest**: [`docs/evaluation/ground_truth_manifest.json`](../ground_truth_manifest.json)
- **Base Commit**: `1c454bb9f787ec3440e6a4a3d47d71d4462b0454`

---

## 1. Objective & Scope

This packet prepares the experimental and operational materials required to execute the approved **Manual Baseline Study Protocol** ([`docs/evaluation/protocol.md`](../protocol.md) §5).

The manual baseline study measures human operator velocity and objective error rates when reconciling wholesale purchase orders without automated software. These empirical measurements will serve as the auditable point of comparison against OrderShield's automated and assisted processing under Success Criteria **SC-001**, **SC-002**, and **SC-003**.

> [!IMPORTANT]
> **Execution Scope**:
>
> **AUTHORIZED**:
> - Manual baseline participant runs in canonical order: **Case A $\to$ Case B $\to$ Case C**;
> - Russian participant packet ([`participant_packet_ru/`](./participant_packet_ru/)) and English packet ([`participant_packet/`](./participant_packet/));
> - Neutral purchase order display filenames (`Заказ_A.txt`, `Заказ_B.txt`, `Заказ_C.txt`);
> - Collection of raw participant timing and reconciliation answers.
>
> **STILL NOT AUTHORIZED**:
> - SC-001 assisted OrderShield run (remains pending canonical 5-line intake path decision);
> - LIVE evaluation provider calls;
> - Top-level README marketing/performance claims updates;
> - Fabricated or inferred participant data.

---

## 2. Directory Structure & Distribution Safety

To strictly prevent accidental answer leakage, materials are isolated into dedicated participant and observer directories.

```
docs/evaluation/manual_study/
├── README.md
├── participant_packet/
│   ├── participant_instructions.md
│   ├── catalog_sheet.md
│   ├── contract_pricing_sheet.md
│   └── reconciliation_template.md
├── participant_packet_ru/
│   ├── participant_instructions_ru.md
│   ├── catalog_sheet_ru.md
│   ├── contract_pricing_sheet_ru.md
│   └── reconciliation_template_ru.md
└── observer_only/
    ├── observer_recording_template.md
    ├── observer_execution_checklist_ru.md
    └── study_results.schema.json
```

### Strict Distribution Safety Rule
> [!CAUTION]
> **NEVER give participants access to the entire repository or the root `docs/evaluation/manual_study/` directory.**
>
> Participant distribution packages must contain **ONLY**:
> 1. English execution: Files from [`participant_packet/`](./participant_packet/) (`participant_instructions.md`, `catalog_sheet.md`, `contract_pricing_sheet.md`, `reconciliation_template.md`).
> 2. Russian-speaking execution: Distribute **ONLY** files from [`participant_packet_ru/`](./participant_packet_ru/) (`participant_instructions_ru.md`, `catalog_sheet_ru.md`, `contract_pricing_sheet_ru.md`, `reconciliation_template_ru.md`). Do not distribute `participant_packet` and `participant_packet_ru` together unless explicitly needed.
> 3. The single purchase order text file currently being executed (Case A, then Case B, then Case C), provided under a neutral display filename (`Заказ_A.txt` / `Case_A.txt`, `Заказ_B.txt` / `Case_B.txt`, `Заказ_C.txt` / `Case_C.txt`). Never expose semantic canonical filenames (`po_discrepancy_apex.txt`, `po_ambiguous_apex.txt`) to participants.
>
> All materials under [`observer_only/`](./observer_only/) containing ground-truth answers, discrepancy keys, scoring criteria, and observer checklists must remain strictly confidential and inaccessible to participants.

---

## 3. Canonical Study Cases

Participants will evaluate exactly the three approved study cases in the specified canonical order:

1. **Case A**: [`docs/evaluation/fixtures/sc001_prepared_5line_po.txt`](../fixtures/sc001_prepared_5line_po.txt)
   - Synthetic 5-line purchase order for Acme Industrial Supplies (`CUST-ACME`).
   - Standardized velocity benchmark fixture for SC-001.
2. **Case B**: [`tests/fixtures/po_discrepancy_apex.txt`](../../../tests/fixtures/po_discrepancy_apex.txt)
   - Stateful 2-line purchase order for Apex Distribution (`CUST-APEX`).
   - Evaluates price mismatch, catalog description ambiguity, and stateful latent rules.
3. **Case C**: [`tests/fixtures/po_ambiguous_apex.txt`](../../../tests/fixtures/po_ambiguous_apex.txt)
   - 1-line purchase order for Apex Distribution (`CUST-APEX`).
   - Evaluates ambiguous description routing to customer clarification.

*Note: No alternative, simplified, or substituted cases may be used.*

---

## 4. Participant Protocol & Boundaries

### Permitted Tools
- Desktop or handheld calculator.
- Basic spreadsheet software (Excel, Google Sheets, LibreOffice Calc) used purely as a display/scratchpad.
- Text editor or document viewer for reading text files.

### Prohibited Tools
- OrderShield web application, API, or database.
- ChatGPT, Claude, Gemini, Copilot, or any other LLM / AI tool.
- Automated reconciliation scripts or regex matching tools.
- Collaboration with other participants or observers.
- Answer keys or ground-truth manifests.

### Execution & Timing Rules
- **Timer Start**: The exact timestamp when the participant opens the PO file and reference sheets.
- **Timer Stop**: The exact timestamp when the participant submits the completed reconciliation template.
- **Execution Order**: Cases must be reconciled independently in the strict order: **Case A $\to$ Case B $\to$ Case C**.

---

## 5. Participant Eligibility & Cohort Design

- **Cohort Size**: 2 to 3 independent human participants.
- **Eligibility Requirement**: Participants must have **no prior knowledge** of the test cases, fixtures, ground-truth manifest, or application reconciliation implementation.
- **Sample Characterization**: Small convenience sample. Participants must not be described as professional wholesale coordinators unless factually true.
- **Privacy & Anonymity**:
  - Non-identifying IDs: `P01`, `P02`, `P03`.
  - Neutral background category: e.g., `"student"`, `"developer"`, `"administrative experience"`.
  - No PII (names, emails, phone numbers) is collected or stored.

---

## 6. Manual Baseline Error Scoring & Case B Reconciliation

Scoring is strictly objective and based on the approved error classifications:
1. **Missed Price Mismatch**: Fails to flag when requested unit price differs from contract tier price.
2. **Missed MOQ Violation**: Fails to flag when requested quantity is below catalog MOQ.
3. **Missed Package Violation**: Fails to flag when quantity violates packaging increment.
4. **Missed Arithmetic Error**: Fails to flag discrepancy between `qty * price` and stated line total.
5. **Incorrect SKU Assignment**: Assigns an arbitrary SKU to an ambiguous description instead of routing for customer clarification.

### Case-by-Case Scored Discrepancies
- **Case A**: **0** scored discrepancies.
- **Case B**: **2 primary scored discrepancies**:
  1. `PriceMismatch` (Line 1): Requested $18.00 vs contracted tier price $22.00.
  2. `Catalog ambiguity / Needs clarification` (Line 2): `"Standard pallet wrap"` matches multiple catalog SKUs (`SKU-WRAP-15` vs `SKU-WRAP-18`).
- **Case C**: **1 primary scored discrepancy**:
  1. `Catalog ambiguity / Needs clarification` (Line 1): `"Standard pallet wrap"` matches multiple catalog SKUs.

### Case B Latent MOQ Semantics in Manual Study
In accordance with accepted stateful domain semantics (ADR 0001 and manifest stateful lifecycle), the packaging/MOQ rule on Line 2 of Case B is **latent** until SKU resolution. Because participant instructions correctly require marking an ambiguous SKU as "Needs clarification" rather than guessing, a participant who marks "Needs clarification" has performed the correct manual action.
- Latent MOQ is retained as a **documented secondary stateful observation** in the observer notes.
- Latent MOQ is **NOT** counted as a participant miss when the participant correctly refuses to choose an ambiguous SKU.
- Latent MOQ is **NOT** included in the primary manual catch-rate denominator.

### Quantitative Measurement Scope & Cognitive Burden Clarification
- **Quantitative Metrics**: Only duration (seconds) and objective reconciliation errors (error classification counts) are quantitatively measured.
- **Cognitive Burden Clarification**: While protocol §5.1 conceptually references operator cognitive burden, no objective psychological or workload measurement instrument (such as NASA-TLX or validated survey instrument) was operationalized in the protocol. To maintain scientific integrity, **cognitive burden is NOT operationalized in this study and must NOT be reported as a measured quantitative result.** No subjective scoring or invented scales are permitted.

---

## 7. Deterministic Aggregation Plan

Before collecting any data, the aggregation methodology is pre-committed:

### Per-Case Aggregation
- **Durations**: Collect $[d_1, d_2, \dots, d_N]$ and calculate the median ($p50$).
- **Error Count**: Sum of all missed discrepancies and incorrect SKU assignments across the cohort.
- **Catch Rate**:
  - Case A (0 seeded): Catch rate N/A; report false positive count.
  - Case B (2 seeded): $\frac{\sum \text{Correctly Identified}}{2N} \times 100\%$.
  - Case C (1 seeded): $\frac{\sum \text{Correctly Identified}}{1N} \times 100\%$.

### Overall Manual Batch Aggregation
- **Total Duration per Participant**: $T_i = d_{A,i} + d_{B,i} + d_{C,i}$.
- **Median Total Batch Duration**: $\text{median}([T_1, \dots, T_N])$.
- **Primary Total Seeded Denominator**: **3 per participant** ($0 + 2 + 1 = 3$), yielding **$3N$ across the batch** ($N=2 \implies 6$; $N=3 \implies 9$).
- **Batch Discrepancy Catch Rate**:
  $$\text{Batch Catch Rate} = \frac{\sum_{i=1}^N \text{Total Caught}_i}{3N} \times 100\%$$
- **Explicit Sample Size**: Reported explicitly as $N=2$ or $N=3$.

### Scientific Integrity & Claims Constraints
- No extrapolation to industry-wide operator baselines.
- No invented cost-savings or margin-recovery dollar projections.
- No claims of statistical significance from small convenience cohorts.

---

## 8. SC-001 Assisted OrderShield Run Protocol

A dedicated section of [`observer_recording_template.md`](./observer_only/observer_recording_template.md) prepares the assisted OrderShield timing measurement:
- **Canonical Fixture**: `docs/evaluation/fixtures/sc001_prepared_5line_po.txt`
- **Timer Start**: Operator initiates submission of the prepared 5-line PO in the OrderShield UI.
- **Timer Stop**: Approval is completed and the `Verified Order` result is visibly returned.
- **SC-001 Pass Condition**: Actual assisted duration $< 60.0$ seconds (FAIL if $\ge 60.0$ seconds).
- **Status in EVAL-03A**: Strictly left blank (`NOT_YET_MEASURED`).
- **Intake Path Precondition**: The current application has no accepted replay extraction asset for `sc001_prepared_5line_po.txt`. Therefore, `clean_acme` must **not** be substituted, and system timing must **not** be fabricated. Execution requires a separate, explicit project decision regarding the 5-line intake path.

---

## 9. Study Design & Contamination Guard

To protect measurement validity:
- **Preferred Cohort Separation**:
  - Manual baseline participants: 2–3 individuals with zero prior answer knowledge.
  - Assisted SC-001 operator: A separate individual who has not manually reconciled Case A beforehand.
- **Contamination Disclosure**: If resource constraints necessitate using the same person for both manual reconciliation of Case A and the assisted OrderShield run, this familiarity bias must be documented explicitly in the observer log and final report.

---

## 10. Execution Governance & Scope Gates

**Status**: **`MANUAL BASELINE EXECUTION AUTHORIZED`**

- **Human Gate HG-EVAL-03A**: **APPROVED** (English study packet and measurement methodology).
- **Human Gate HG-EVAL-03A-RU**: **APPROVED** (Russian participant localization, neutral case naming, and observer operational checklist).
- Manual participant execution across Cases A, B, and C may proceed following the procedures in [`observer_execution_checklist_ru.md`](./observer_only/observer_execution_checklist_ru.md).
- SC-001 assisted OrderShield measurement remains blocked pending canonical 5-line intake path decision.
- LIVE evaluation modes remain disabled.
- Top-level marketing/performance claims remain strictly locked until empirical results are committed and reviewed.
