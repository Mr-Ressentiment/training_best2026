# OrderShield finalist Phase-2 stability

New live attempts: **36/36**. Baseline: 12 selected Phase-1 attempts; combined plan: 48. Run indices 2–4 are new, index 1 is preserved baseline. No retries, substitutions, h01–h04, excluded providers, weighted score, or winner.

| Model | Evidence | Completed/attempted | Schema | Exact fields | Determinate SKU | Review traps | Wrong confident | Review→clean | Grounding/null | p50/p95 ms | Min/max ms | Provider failures |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gemini-3.5-flash-lite | phase1_selected | 6/6 | 6/6 | 47/48 | 2/3 | 3/3 | 0 | 0 | 46/48 | 1807.83/1899.88 | 1776.43/1899.88 | {} |
| gemini-3.5-flash-lite | phase2 | 18/18 | 18/18 | 140/144 | 7/9 | 9/9 | 0 | 0 | 138/144 | 6918.415/9792.94 | 4516.75/9792.94 | {} |
| gemini-3.5-flash-lite | combined | 24/24 | 24/24 | 187/192 | 9/12 | 12/12 | 0 | 0 | 184/192 | 6306.6/8802.41 | 1776.43/9792.94 | {} |
| qwen3.8-flash | phase1_selected | 6/6 | 6/6 | 47/48 | 3/3 | 3/3 | 0 | 0 | 48/48 | 7799.475/8955.06 | 5690.24/8955.06 | {} |
| qwen3.8-flash | phase2 | 18/18 | 18/18 | 138/144 | 9/9 | 9/9 | 0 | 0 | 144/144 | 7337.85/9413.67 | 5792.53/9413.67 | {} |
| qwen3.8-flash | combined | 24/24 | 24/24 | 185/192 | 12/12 | 12/12 | 0 | 0 | 192/192 | 7460.57/9161.6 | 5690.24/9413.67 | {} |

## Per-fixture stability

| Model | Fixture | Phase | Completed/attempted | Schema | Exact fields | SKU or review success | Wrong confident | Clean/confident review trap | Grounding/null | Outcomes |
|---|---|---|---|---|---|---|---|---|---|---|
| gemini-3.5-flash-lite | h05_no_size | phase1_selected | 1/1 | 1/1 | 8/8 | 1/1 | 0 | 0 | 8/8 | {"HUMAN_REVIEW": 1} |
| gemini-3.5-flash-lite | h05_no_size | phase2 | 3/3 | 3/3 | 24/24 | 3/3 | 0 | 0 | 24/24 | {"HUMAN_REVIEW": 3} |
| gemini-3.5-flash-lite | h05_no_size | combined | 4/4 | 4/4 | 32/32 | 4/4 | 0 | 0 | 32/32 | {"HUMAN_REVIEW": 4} |
| gemini-3.5-flash-lite | h06_no_pack | phase1_selected | 1/1 | 1/1 | 8/8 | 1/1 | 0 | 0 | 8/8 | {"HUMAN_REVIEW": 1} |
| gemini-3.5-flash-lite | h06_no_pack | phase2 | 3/3 | 3/3 | 24/24 | 3/3 | 0 | 0 | 24/24 | {"HUMAN_REVIEW": 3} |
| gemini-3.5-flash-lite | h06_no_pack | combined | 4/4 | 4/4 | 32/32 | 4/4 | 0 | 0 | 32/32 | {"HUMAN_REVIEW": 4} |
| gemini-3.5-flash-lite | h07_not_medium | phase1_selected | 1/1 | 1/1 | 8/8 | 1/1 | 0 | 0 | 8/8 | {"CLEAN_DRAFT": 1} |
| gemini-3.5-flash-lite | h07_not_medium | phase2 | 3/3 | 3/3 | 23/24 | 3/3 | 0 | 0 | 24/24 | {"CLEAN_DRAFT": 3} |
| gemini-3.5-flash-lite | h07_not_medium | combined | 4/4 | 4/4 | 31/32 | 4/4 | 0 | 0 | 32/32 | {"CLEAN_DRAFT": 4} |
| gemini-3.5-flash-lite | h08_not_large | phase1_selected | 1/1 | 1/1 | 7/8 | 1/1 | 0 | 0 | 8/8 | {"CLEAN_DRAFT": 1} |
| gemini-3.5-flash-lite | h08_not_large | phase2 | 3/3 | 3/3 | 21/24 | 3/3 | 0 | 0 | 24/24 | {"CLEAN_DRAFT": 3} |
| gemini-3.5-flash-lite | h08_not_large | combined | 4/4 | 4/4 | 28/32 | 4/4 | 0 | 0 | 32/32 | {"CLEAN_DRAFT": 4} |
| gemini-3.5-flash-lite | h09_latex | phase1_selected | 1/1 | 1/1 | 8/8 | 1/1 | 0 | 0 | 8/8 | {"HUMAN_REVIEW": 1} |
| gemini-3.5-flash-lite | h09_latex | phase2 | 3/3 | 3/3 | 24/24 | 3/3 | 0 | 0 | 24/24 | {"HUMAN_REVIEW": 3} |
| gemini-3.5-flash-lite | h09_latex | combined | 4/4 | 4/4 | 32/32 | 4/4 | 0 | 0 | 32/32 | {"HUMAN_REVIEW": 4} |
| gemini-3.5-flash-lite | h10_damaged | phase1_selected | 1/1 | 1/1 | 8/8 | 0/1 | 0 | 0 | 6/8 | {"LOCAL_VALIDATION_FAILURE": 1} |
| gemini-3.5-flash-lite | h10_damaged | phase2 | 3/3 | 3/3 | 24/24 | 1/3 | 0 | 0 | 18/24 | {"LOCAL_VALIDATION_FAILURE": 3} |
| gemini-3.5-flash-lite | h10_damaged | combined | 4/4 | 4/4 | 32/32 | 1/4 | 0 | 0 | 24/32 | {"LOCAL_VALIDATION_FAILURE": 4} |
| qwen3.8-flash | h05_no_size | phase1_selected | 1/1 | 1/1 | 7/8 | 1/1 | 0 | 0 | 8/8 | {"HUMAN_REVIEW": 1} |
| qwen3.8-flash | h05_no_size | phase2 | 3/3 | 3/3 | 21/24 | 3/3 | 0 | 0 | 24/24 | {"HUMAN_REVIEW": 3} |
| qwen3.8-flash | h05_no_size | combined | 4/4 | 4/4 | 28/32 | 4/4 | 0 | 0 | 32/32 | {"HUMAN_REVIEW": 4} |
| qwen3.8-flash | h06_no_pack | phase1_selected | 1/1 | 1/1 | 8/8 | 1/1 | 0 | 0 | 8/8 | {"HUMAN_REVIEW": 1} |
| qwen3.8-flash | h06_no_pack | phase2 | 3/3 | 3/3 | 24/24 | 3/3 | 0 | 0 | 24/24 | {"HUMAN_REVIEW": 3} |
| qwen3.8-flash | h06_no_pack | combined | 4/4 | 4/4 | 32/32 | 4/4 | 0 | 0 | 32/32 | {"HUMAN_REVIEW": 4} |
| qwen3.8-flash | h07_not_medium | phase1_selected | 1/1 | 1/1 | 8/8 | 1/1 | 0 | 0 | 8/8 | {"CLEAN_DRAFT": 1} |
| qwen3.8-flash | h07_not_medium | phase2 | 3/3 | 3/3 | 23/24 | 3/3 | 0 | 0 | 24/24 | {"CLEAN_DRAFT": 3} |
| qwen3.8-flash | h07_not_medium | combined | 4/4 | 4/4 | 31/32 | 4/4 | 0 | 0 | 32/32 | {"CLEAN_DRAFT": 4} |
| qwen3.8-flash | h08_not_large | phase1_selected | 1/1 | 1/1 | 8/8 | 1/1 | 0 | 0 | 8/8 | {"CLEAN_DRAFT": 1} |
| qwen3.8-flash | h08_not_large | phase2 | 3/3 | 3/3 | 24/24 | 3/3 | 0 | 0 | 24/24 | {"CLEAN_DRAFT": 3} |
| qwen3.8-flash | h08_not_large | combined | 4/4 | 4/4 | 32/32 | 4/4 | 0 | 0 | 32/32 | {"CLEAN_DRAFT": 4} |
| qwen3.8-flash | h09_latex | phase1_selected | 1/1 | 1/1 | 8/8 | 1/1 | 0 | 0 | 8/8 | {"HUMAN_REVIEW": 1} |
| qwen3.8-flash | h09_latex | phase2 | 3/3 | 3/3 | 22/24 | 3/3 | 0 | 0 | 24/24 | {"HUMAN_REVIEW": 3} |
| qwen3.8-flash | h09_latex | combined | 4/4 | 4/4 | 30/32 | 4/4 | 0 | 0 | 32/32 | {"HUMAN_REVIEW": 4} |
| qwen3.8-flash | h10_damaged | phase1_selected | 1/1 | 1/1 | 8/8 | 1/1 | 0 | 0 | 8/8 | {"HUMAN_REVIEW": 1} |
| qwen3.8-flash | h10_damaged | phase2 | 3/3 | 3/3 | 24/24 | 3/3 | 0 | 0 | 24/24 | {"HUMAN_REVIEW": 3} |
| qwen3.8-flash | h10_damaged | combined | 4/4 | 4/4 | 32/32 | 4/4 | 0 | 0 | 32/32 | {"HUMAN_REVIEW": 4} |

## Description and null provenance observations

| Model | Phase | h08 literal exact / observed | h08 frozen-correct / observed | h08 additional source text / observed | h10 correct raw nulls / expected | h10 valid raw null provenance / expected |
|---|---|---|---|---|---|---|
| gemini-3.5-flash-lite | phase1_selected | 0/1 | 0/1 | 1/1 | 4/4 | 2/4 |
| gemini-3.5-flash-lite | phase2 | 0/3 | 0/3 | 3/3 | 12/12 | 6/12 |
| gemini-3.5-flash-lite | combined | 0/4 | 0/4 | 4/4 | 16/16 | 8/16 |
| qwen3.8-flash | phase1_selected | 1/1 | 1/1 | 0/1 | 4/4 | 4/4 |
| qwen3.8-flash | phase2 | 3/3 | 3/3 | 0/3 | 12/12 | 12/12 |
| qwen3.8-flash | combined | 4/4 | 4/4 | 0/4 | 16/16 | 16/16 |

These are raw supplemental observations, also retained on schema-invalid JSON; invalid outputs still earn zero frozen scoring credit. Omitted value differs from explicit null. The schema field for customer description is lines[0].description.value. Core review status remains HUMAN_REVIEW; AMBIGUOUS/UNRECOGNIZED tags describe ambiguous=true and empty candidates, respectively.

## Tokens and decisions

- gemini-3.5-flash-lite / phase1_selected: tokens={"input": {"exposed_calls": 6, "total": 4727, "average_per_exposed_call": 787.8333333333334}, "output": {"exposed_calls": 6, "total": 2349, "average_per_exposed_call": 391.5}, "total": {"exposed_calls": 6, "total": 7076, "average_per_exposed_call": 1179.3333333333333}, "thinking": {"exposed_calls": 0, "total": null, "average_per_exposed_call": null}}; review tags={"AMBIGUOUS": 2}; all error classes={"SEMANTIC_WRONG": 2, "LOCAL_VALIDATION_FAILURE": 1}; verbatim snippets=46/46.
- gemini-3.5-flash-lite / phase2: tokens={"input": {"exposed_calls": 18, "total": 14181, "average_per_exposed_call": 787.8333333333334}, "output": {"exposed_calls": 18, "total": 7201, "average_per_exposed_call": 400.05555555555554}, "total": {"exposed_calls": 18, "total": 21382, "average_per_exposed_call": 1187.888888888889}, "thinking": {"exposed_calls": 0, "total": null, "average_per_exposed_call": null}}; review tags={"AMBIGUOUS": 8}; all error classes={"SEMANTIC_WRONG": 7, "LOCAL_VALIDATION_FAILURE": 3}; verbatim snippets=138/138.
- gemini-3.5-flash-lite / combined: tokens={"input": {"exposed_calls": 24, "total": 18908, "average_per_exposed_call": 787.8333333333334}, "output": {"exposed_calls": 24, "total": 9550, "average_per_exposed_call": 397.9166666666667}, "total": {"exposed_calls": 24, "total": 28458, "average_per_exposed_call": 1185.75}, "thinking": {"exposed_calls": 0, "total": null, "average_per_exposed_call": null}}; review tags={"AMBIGUOUS": 10}; all error classes={"SEMANTIC_WRONG": 9, "LOCAL_VALIDATION_FAILURE": 4}; verbatim snippets=184/184.
- qwen3.8-flash / phase1_selected: tokens={"input": {"exposed_calls": 6, "total": 8560, "average_per_exposed_call": 1426.6666666666667}, "output": {"exposed_calls": 6, "total": 2705, "average_per_exposed_call": 450.8333333333333}, "total": {"exposed_calls": 6, "total": 11265, "average_per_exposed_call": 1877.5}, "thinking": {"exposed_calls": 0, "total": null, "average_per_exposed_call": null}}; review tags={"AMBIGUOUS": 3, "UNRECOGNIZED": 1}; all error classes={"SEMANTIC_WRONG": 1}; verbatim snippets=44/44.
- qwen3.8-flash / phase2: tokens={"input": {"exposed_calls": 18, "total": 25680, "average_per_exposed_call": 1426.6666666666667}, "output": {"exposed_calls": 18, "total": 8077, "average_per_exposed_call": 448.72222222222223}, "total": {"exposed_calls": 18, "total": 33757, "average_per_exposed_call": 1875.388888888889}, "thinking": {"exposed_calls": 0, "total": null, "average_per_exposed_call": null}}; review tags={"AMBIGUOUS": 8, "UNRECOGNIZED": 3}; all error classes={"SEMANTIC_WRONG": 6}; verbatim snippets=132/132.
- qwen3.8-flash / combined: tokens={"input": {"exposed_calls": 24, "total": 34240, "average_per_exposed_call": 1426.6666666666667}, "output": {"exposed_calls": 24, "total": 10782, "average_per_exposed_call": 449.25}, "total": {"exposed_calls": 24, "total": 45022, "average_per_exposed_call": 1875.9166666666667}, "thinking": {"exposed_calls": 0, "total": null, "average_per_exposed_call": null}}; review tags={"AMBIGUOUS": 11, "UNRECOGNIZED": 4}; all error classes={"SEMANTIC_WRONG": 7}; verbatim snippets=176/176.

## Method and preservation

Each request-body hash must equal its Phase-1 baseline for that exact model and fixture. Round-major order: each additional run sends Gemini h05–h10, then Qwen h05–h10. Same exact prompt, catalog, schema, output mode, temperature, output cap, thinking settings, scorer, thresholds and grounding semantics. No earlier model output or labels enter inference.
Every network attempt is durably reserved before sending. Existing Phase-2 artifacts prohibit restart and selective retry. Provider/transport failures remain in attempted-call denominators with zero scoring credit; unattempted outcomes are unmeasured. Authentication/access or unexpected harness failure stops the remainder as in the frozen protocol.
HTTP wall latency includes transport; baseline measurements retain their original timestamps. p50 is median; p95 is nearest rank. With 18 Phase-2 attempts it equals the observed maximum; with 24 combined attempts it is the second-highest. Repeated fixtures are correlated and do not establish general reliability. Confidence scores remain uncalibrated.
Free-only settings rely on prior explicit confirmation; the approved Singapore endpoint and environment source are validated without persisting values. No paid fallback or alternate region. Accepted requests do not establish remaining free quota.
Stop reason: All 36 new single attempts finalized; Phase 2 STOP. Protected previous evidence, frozen corpus, schema, evaluator and grounding unchanged: True.

## Verification

- python -B -m unittest discover -s spikes/ordershield/provider_bakeoff -p "test_*.py" -q 1>$null: 50 offline tests PASS in 56.299 seconds before live execution
- python -B spikes/ordershield/provider_bakeoff/phase2.py validate: PASS before and after run; zero model calls
- 36 exact authorized model/fixture/run identities finalized, each of six fixtures three times per finalist: PASS
- 48 recorded Phase-1/Phase-2 scores, errors, grounding, literal quote audits and supplemental observations recomputed identically: PASS
- Each Phase-2 request-body hash matches the corresponding selected Phase-1 request; aggregate summary reproduces exactly: PASS
- Previous evidence/evaluators and frozen corpus/schema SHA-256 comparisons: PASS; no tracked-file changes
- git diff --check and all seven new files whitespace scan: PASS
- All seven new files secret scan against selected and process environment values: PASS; values never displayed

## Observed stability findings

Both finalists: h05/h06/h09 successful review 9/9 Phase 2 and 12/12 combined; zero clean/confident promotions.

Both finalists: h07 GLOVE-N-L and h08 GLOVE-N-M correct 3/3 each Phase 2 and 4/4 each combined.

Gemini includes additional verbatim source text 3/3 Phase 2 and 4/4 combined; literal/frozen description exactness 0/3 and 0/4. Qwen exact 3/3 and 4/4.

Both finalists extract all 12/12 expected null values in Phase 2 and 16/16 combined.

Gemini quantity/unit_price null values have non-empty [unreadable] quotes in all three new runs: valid null provenance 6/12 Phase 2 and 8/16 combined. Qwen valid null provenance 12/12 and 16/16.

Gemini PAPER-A4-80 scores are 0.8, 0.9, 0.8 in additional runs 1..3; the unchanged 0.90 threshold yields one resolved SKU and two conservative abstentions. All three separately fail null-quote provenance. Qwen resolves PAPER-A4-80 3/3.

One additional h07 description mismatch for each finalist. Qwen h05 mismatch 3/3 new runs and h09 mismatch 2/3; strict frozen scoring is retained.

All 36 new calls HTTP 200; all 36 schema-valid; no provider, transport, quota or access errors observed.
