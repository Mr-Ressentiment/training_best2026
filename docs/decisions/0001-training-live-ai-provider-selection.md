# ADR 0001: Training & Demo Live AI Provider Selection and Runtime Timeout Reconciliation

- **Status**: Accepted (Human Gate Approved)
- **Date**: 2026-09-30
- **Deciders**: Project Brain, Integrator (Human Gate), Fast Executor (Antigravity)
- **Scope**: OrderShield Purchase Order Reconciliation (Feature `001-ordershield-po-reconciliation`)
- **Canonical Evidence**: `spikes/ordershield/provider_bakeoff/`

---

## 1. Context & Problem Statement

OrderShield requires an external LLM provider for live document extraction and semantic SKU candidate mapping (`OrderShieldAIProvider`). Architectural Gate 4 initially established a pluggable, provider-neutral protocol with untrusted schema validation and an assumed client-side timeout of ≤4.5 seconds to align with specification criterion SC-006 (diagnostic failure within ≤5 seconds).

To select a concrete model for training, development, and live demonstration without commercial API expenses, a free-tier provider bake-off was executed across synthetic purchase order fixtures (`h01` through `h10`), evaluating preflight viability, Phase-1 multi-provider extraction fidelity, Gemini diagnostic remediation, and Phase-2 finalist stability.

With the empirical bake-off concluded, this record reconciles:
1. The selection of the primary live provider and fallback candidate for training and demo execution.
2. The empirical evidence justifying the decision.
3. Explicit operational limitations of the free-tier evaluation.
4. The runtime timeout reconciliation between observed live inference latency and SC-006 explicit diagnostic failure handling.

---

## 2. Decision Outcome

### 2.1 Provider Selection

1. **PRIMARY TRAINING LIVE PROVIDER**:
   - **Model**: Alibaba Qwen 3.8 Flash (`qwen3.8-flash`) via DashScope / OpenAI-compatible endpoint.
   - **Configuration**: Reasoning / thinking disabled.
   - **Role**: Primary provider for live interactive intake, verification testing, and demonstration.

2. **SECONDARY / FALLBACK CANDIDATE**:
   - **Model**: Google Gemini 3.5 Flash-Lite (`gemini-3.5-flash-lite`) via Google GenAI API.
   - **Configuration**: Minimal thinking.
   - **Role**: Validated secondary provider candidate if primary training provider experiences localized disruption.

3. **NOT SELECTED FOR CURRENT TRAINING LIVE PATH**:
   - **Model**: Google Gemini 3.8 Flash (`gemini-3.8-flash`).
   - **Reason**: Repeated free-tier HTTP 503 / availability failure evidence during bake-off execution.
   - **Explicit Clarification**: This is strictly an operational availability rejection under free-tier quota constraints; it is **NOT** a model-quality or reasoning-capability rejection.

4. **PREFLIGHT-ONLY CANDIDATE**:
   - **Model**: Google Gemini 3.7 Flash (`gemini-3.7-flash`).
   - **Status**: Passed preflight smoke testing with ~13.7 s minimal smoke latency; not advanced into the full comparative bake-off.

### 2.2 Fallback Semantics & Provider Governance

While Google Gemini 3.5 Flash-Lite is validated as a secondary/fallback candidate, the following architectural invariants are strictly enforced:
- **No Automatic Runtime Provider Failover**: The application runtime does **NOT** perform dynamic or automatic provider failover. If a live inference request to the configured primary provider fails, the system returns an explicit diagnostic error (`AIProviderUnavailableError`) and terminates processing; it does not silently redirect the request to the secondary provider.
- **No Silent Provider Substitution**: The system must never substitute one live provider or model for another at runtime without explicit operator instruction, just as it must never silently fall back to fixtures.
- **Explicit Operator Action Required**: Switching providers (e.g. from Qwen 3.8 Flash to Gemini 3.5 Flash-Lite) requires explicit administrative/operator action via configuration (`LLM_PROVIDER=gemini`) and service restart.
- **Unambiguous Provenance Tracking**: Extracted drafts, audit trail events, and provenance metadata must capture the exact provider and model identifier used during inference (e.g., `actor: "AIProvider"`, `metadata.provider: "qwen"`, `metadata.model: "qwen3.8-flash"`).

---

## 3. Empirical Finalist Evidence

### 3.1 Combined Selected-Fixture Summary (Phase 1 Baseline + Phase 2 Stability)

Across 24 completed attempts per finalist on selected challenging fixtures (`h05` through `h10`):

| Metric | Alibaba Qwen 3.8 Flash | Google Gemini 3.5 Flash-Lite |
|:---|:---:|:---:|
| **Completed / Attempted** | 24 / 24 (100%) | 24 / 24 (100%) |
| **Exact Mandatory Fields** | 185 / 192 (96.4%) | 187 / 192 (97.4%) |
| **Determinate SKU Selection** | 12 / 12 (100%) | 9 / 12 (75.0%) |
| **Review Traps Correctly Caught** | 12 / 12 (100%) | 12 / 12 (100%) |
| **Grounding / Null Validity** | 192 / 192 (100%) | 184 / 192 (95.8%) |
| **Wrong Confident SKUs** | **0** (Zero hallucinated SKUs) | **0** (Zero hallucinated SKUs) |
| **Ambiguous-to-Clean Promotions** | **0** (Zero unreviewed ambiguities) | **0** (Zero unreviewed ambiguities) |

### 3.2 Repeated Fixture Findings

- **Review Traps (`h05_no_size`, `h06_no_pack`, `h09_latex`)**:
  Both finalists routed 100% of runs (4/4 each) to human review (`HUMAN_REVIEW`), successfully refusing clean draft promotion on underspecified or out-of-catalog items.
- **Distinguishing SKUs (`h07_not_medium`, `h08_not_large`)**:
  Both finalists correctly resolved the required distinguishing SKUs (`GLOVE-N-L` on `h07`, `GLOVE-N-M` on `h08`) across 4/4 runs each.
- **Customer Description Exactness (`h08`)**:
  - Gemini: 0/4 exact due to inclusion of extraneous source document text surrounding the item description.
  - Qwen: 4/4 exact description extraction.
- **Missing / Damaged Line Handling (`h10_damaged`)**:
  - Both finalists extracted all 16/16 expected null values correctly.
  - Null provenance citations: Qwen achieved 16/16 valid null provenance; Gemini achieved 8/16 (emitting `"[unreadable]"` quote tokens for null quantities and prices).
  - SKU resolution on damaged lines: Qwen resolved `PAPER-A4-80` 4/4; Gemini resolved 1/4 with 3 conservative abstentions below confidence threshold.

### 3.3 Latency Profile (Phase 2 Wall-Clock Time)

- **Google Gemini 3.5 Flash-Lite**:
  - Median (p50): 6.918 s
  - Observed Max: 9.793 s
- **Alibaba Qwen 3.8 Flash**:
  - Median (p50): 7.338 s
  - Observed Max: 9.414 s

Both finalists exhibited stable, predictable latency in the 6.9–9.8 s range across all live attempts.

---

## 4. Operational Limitations

The following limitations are explicitly recorded and govern the interpretation of this decision:
1. **Synthetic Frozen Corpus**: Evaluation was conducted on a controlled, synthetic frozen corpus of 10 purchase order documents (`h01`–`h10`).
2. **Selected Fixture Repetition**: Repeated stability testing in Phase 2 was conducted exclusively on fixtures `h05` through `h10`.
3. **Statistical Sample Size**: Four observations per selected fixture (1 Phase-1 baseline + 3 Phase-2 runs) demonstrate short-term stability but do not establish production-grade statistical reliability.
4. **Free-Tier Constraints**: Successful free-tier availability and quota windows do not establish production enterprise suitability or SLA guarantees.
5. **Paid Candidates Untested**: Commercial paid enterprise models (e.g., GPT-4o, Claude 3.5 Sonnet, paid Gemini 1.5 Pro) were researched but not empirically benchmarked in this bake-off.
6. **Training/Demo Scope**: Alibaba Qwen 3.8 Flash is selected strictly as the **TRAINING and DEMO** live provider.
7. **Production Undecided**: Commercial production provider selection remains an open decision gate for future phases.

---

## 5. Runtime Timeout Reconciliation & Approved SC-006 Semantics

### 5.1 Problem with Earlier Planning Assumption

Prior planning artifacts assumed a universal client-side timeout of `≤4.5 s` for live AI calls, derived from an assumption that all provider interactions would conclude within specification criterion SC-006 ("presents an explicit diagnostic error within 5 seconds").

The empirical bake-off evidence demonstrates that neither candidate model can complete full document extraction and semantic candidate scoring within 4.5 seconds (observed p50 ~7.0–7.3 s, observed max ~9.4–9.8 s). Enforcing a 4.5 s universal timeout would cause 100% false-positive service failures on healthy live inferences.

### 5.2 Reconciled Runtime Policy & Approved SC-006 Semantics (Human Gate)

To align implementation planning with empirical evidence while maintaining fidelity to the repository constitution, a Human Gate decision was formally approved by the integrator to minimally amend SC-006 runtime semantics across four accepted categories:

1. **Immediately Detectable Failures (Target ≤5 s from request intake)**:
   - Unreadable/local document failure;
   - Connection refusal;
   - Authentication failure;
   - Quota/rate-limit rejection;
   - Explicit upstream 4xx/5xx;
   must surface an explicit diagnostic error with a target of ≤5 seconds from request intake.

2. **Healthy Live Inference (Bounded ≤15 s budget)**:
   - May execute within a bounded ≤15 second live inference budget based on empirical bake-off latency.

3. **Silent / Stalled Provider Inference (Aborted at ≤15 s)**:
   - Silent or stalled provider inference may remain indistinguishable from healthy slow inference until the bounded client deadline.
   - It must be aborted at ≤15 seconds and return an explicit diagnostic error, with no partial persistence and no silent fallback.

4. **Zero Automatic Failover or Fixture Substitution**:
   - No automatic provider failover or fixture substitution is permitted under any failure condition.

---

## 6. References & Related Documents

- Feature Specification: `specs/001-ordershield-po-reconciliation/spec.md`
- Implementation Plan: `specs/001-ordershield-po-reconciliation/plan.md`
- Feature Research: `specs/001-ordershield-po-reconciliation/research.md`
- Provider Bake-Off Evidence:
  - Preflight: `spikes/ordershield/provider_bakeoff/preflight.md`
  - Phase 1 Baseline: `spikes/ordershield/provider_bakeoff/phase1.md`
  - Gemini Diagnostic: `spikes/ordershield/provider_bakeoff/gemini_diagnostic.md`
  - Gemini Remediation: `spikes/ordershield/provider_bakeoff/phase1_gemini_remediation.md`
  - Phase 2 Finalist Stability: `spikes/ordershield/provider_bakeoff/phase2.md`
