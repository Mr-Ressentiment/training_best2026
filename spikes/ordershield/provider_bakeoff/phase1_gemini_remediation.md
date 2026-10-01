# OrderShield Gemini Phase-1 remediation

New live attempts: **10/19**. Evaluated results: **11/20**, including **1** verified corrected Flash-Lite h01 diagnostic. No Qwen calls, retries, substitutions, winner, weighted score, or Phase 2.

**ORIGINAL GEMINI PHASE 1 = invalid harness request; not model-quality evidence.** Those immutable results rejected the MIME string before inference.

**GEMINI REMEDIATION = corrected request path and valid model-quality evidence for returned outputs.** Provider/transport failures remain operational failures. The shared MIME enum is APPLICATION_JSON; all frozen schema and scorer semantics remain unchanged.

| Candidate | Completed / attempted | New / reused | Schema | Mandatory fields | Determinate SKU | Review | Wrong confident | Evidence/null validity | p50 ms | p95/max ms* | Min/max ms |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gemini-3.5-flash-lite | 10/10 | 9/1 | 10/10 (100.0%) | 79/80 (98.8%) | 6/7 (85.7%) | 3/3 (100.0%) | 0 | 78/80 (97.5%) | 1808.9 | 2006.6 | 1776.43/2006.6 |
| gemini-3.8-flash | 0/1 | 1/0 | 0/1 (0.0%) | 0/8 (0.0%) | 0/1 (0.0%) | unmeasured | 0 | 0/8 (0.0%) | 3691.99 | 3691.99 | 3691.99/3691.99 |

*With ten observations nearest-rank p95 equals the observed maximum; it is not a reliable population tail estimate. HTTP wall latency includes the reused diagnostic’s original measurement.

## h05–h09

| Candidate | Fixture | HTTP | Schema | Fields / 8 | Proposed SKU | Review correct | Wrong confident | Grounding | Error |
|---|---|---|---|---|---|---|---|---|---|
| gemini-3.5-flash-lite | h05_no_size | 200 | True | 8 | None | True | False | True | PASS |
| gemini-3.5-flash-lite | h06_no_pack | 200 | True | 8 | None | True | False | True | PASS |
| gemini-3.5-flash-lite | h07_not_medium | 200 | True | 8 | GLOVE-N-L | False | False | True | PASS |
| gemini-3.5-flash-lite | h08_not_large | 200 | True | 7 | GLOVE-N-M | False | False | True | SEMANTIC_WRONG |
| gemini-3.5-flash-lite | h09_latex | 200 | True | 8 | None | True | False | True | PASS |
| gemini-3.8-flash | h05_no_size | unattempted | — | — | — | — | — | — | — |
| gemini-3.8-flash | h06_no_pack | unattempted | — | — | — | — | — | — | — |
| gemini-3.8-flash | h07_not_medium | unattempted | — | — | — | — | — | — | — |
| gemini-3.8-flash | h08_not_large | unattempted | — | — | — | — | — | — | — |
| gemini-3.8-flash | h09_latex | unattempted | — | — | — | — | — | — | — |

## Gates and usage

- **gemini-3.5-flash-lite**: all seven hard gates pass=False; gates={"zero_wrong_confident_skus": true, "h05_h06_h09_successful_review": true, "h07_h08_correct_distinguishing_sku": true, "zero_ambiguous_clean_promotions": true, "every_returned_snippet_verbatim": true, "all_mandatory_fields_match": false, "all_evaluated_outputs_schema_valid": true}.
  Provider failures: {}; all error classes: {"SEMANTIC_WRONG": 2, "LOCAL_VALIDATION_FAILURE": 1}.
  Token totals and averages per exposed call: {"input": {"exposed_calls": 10, "total": 7899, "average_per_exposed_call": 789.9}, "output": {"exposed_calls": 10, "total": 3921, "average_per_exposed_call": 392.1}, "total": {"exposed_calls": 10, "total": 11820, "average_per_exposed_call": 1182}, "thinking": {"exposed_calls": 0, "total": null, "average_per_exposed_call": null}}.
  Returned verbatim snippets: 78/78 (100.0%); non-null grounded coverage: 76/76 (100.0%).
  Strict description mismatches: ["h08_not_large"].
- **gemini-3.8-flash**: all seven hard gates pass=None; gates={"zero_wrong_confident_skus": null, "h05_h06_h09_successful_review": null, "h07_h08_correct_distinguishing_sku": null, "zero_ambiguous_clean_promotions": null, "every_returned_snippet_verbatim": null, "all_mandatory_fields_match": null, "all_evaluated_outputs_schema_valid": null}.
  Provider failures: {"PROVIDER_5XX": 1}; all error classes: {"PROVIDER_5XX": 1}.
  Token totals and averages per exposed call: {"input": {"exposed_calls": 0, "total": null, "average_per_exposed_call": null}, "output": {"exposed_calls": 0, "total": null, "average_per_exposed_call": null}, "total": {"exposed_calls": 0, "total": null, "average_per_exposed_call": null}, "thinking": {"exposed_calls": 0, "total": null, "average_per_exposed_call": null}}.
  Returned verbatim snippets: unmeasured; non-null grounded coverage: 0/8 (0.0%).
  Strict description mismatches: [].

## Method and preservation

h01 reuse verified exact frozen inputs, corrected request-body hash, minimal configuration, unchanged harness/schema, and identical recomputed score and grounding. Provenance records the diagnostic artifact hash and call index 2; original latency and token usage are retained.
Candidate-major order: Flash-Lite h02–h10, then 3.8 h01 and h02–h10 only after h01 HTTP 200 plus JSON/schema validity. Each new attempt is reserved durably before send. Existing remediation artifacts prevent restart or selective retry. Model-quality errors do not change requests or scoring.
Qwen’s ten stored responses reproduce exactly the same scores, errors, and grounding offline. No Qwen model is called. Frozen 0.90 threshold / 0.15 margin, field comparison, and punctuation-safe literal grounding are unchanged. AI receives only frozen synthetic prompt/catalog/fixture; arithmetic and decisions are local.
Failed/invalid outputs receive zero scoring credit. Unattempted results are unmeasured and prevent a complete hard-gate assessment. Empty quotes on correctly missing null fields are valid missingness, not positive evidence. Every returned literal quote is also audited even after schema rejection.
Free-only account settings rely on the prior explicit user confirmation. Accepted calls do not establish remaining quota. Ten fixtures and uncalibrated confidence scores do not establish broad reliability.
Stop reason: Gemini 3.8 h01 did not satisfy HTTP 200 + JSON/schema gate; remaining nine calls not made. Protected prior evidence/corpus/harness hashes unchanged: True.

## Verification

- python -B -m unittest discover -s spikes/ordershield/provider_bakeoff -p "test_*.py" -q: 42 tests PASS before live calls and after the run (post-run: 36.751 seconds)
- python -B spikes/ordershield/provider_bakeoff/phase1_gemini_remediation.py validate: PASS; no live calls
- git diff --check: PASS; all six new files also passed whitespace scan
- Artifact audit: 10 new attempts + 1 reused result, 11 unique model/fixture pairs, summary reproduces exactly, protected evidence and frozen SHA-256 hashes PASS
- All six new files secret scan: PASS; environment values inspected only in memory
- Live run stopped at the Gemini 3.8 h01 gate (HTTP 503); remaining nine calls unattempted; zero retries

## Observed failures

Correct GLOVE-N-M and literal evidence, but description value includes the extra source sentence: Large gloves were ordered last month; that size must not be repeated here. Strict frozen description comparison fails (7/8 fields).

All eight values match, but quantity and unit_price are null with non-empty [unreadable] quotes. Frozen grounding requires an empty quote for null. Core rejects the order, so determinate-SKU scoring receives no credit.

Gemini 3.8 h01: HTTP 503 / UNAVAILABLE / PROVIDER_5XX. Sanitized message: This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later. No model response or usage was exposed; h02 through h10 are unattempted. No additional model-specific request defect was established.
