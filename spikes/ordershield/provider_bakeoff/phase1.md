# OrderShield free-provider Phase 1

**Stopped at exactly 30 live attempts: 10 per candidate, run index 1. Ten model responses completed. No retries, substitutions, winner, weighted score, or Phase 2.**

Both Gemini configurations returned HTTP 400 on every request. Their semantic zero-credit rates reflect provider rejection, and their latencies measure error responses. This run does not establish Gemini extraction quality. The exact rejection reason is UNVERIFIED: the harness retained status/classification but omitted HTTP error bodies. No replacement calls were made.

Qwen returned ten outputs. h01 violates the unchanged schema (extra description key and scores instead of score). h05 routes correctly to review but includes an additional verbatim size-blank sentence in its description, failing the frozen strict field comparison. **No candidate passes every hard semantic gate in this run.**

## Metrics

| Candidate | Attempted/completed | Schema | Mandatory fields | Whole order fields | Determinate SKU | Review h05/h06/h09 | Wrong confident | Evidence/null validity | Non-null evidence |
|---|---|---|---|---|---|---|---|---|---|
| gemini-3.5-flash-lite | 10/0 | 0/10 | 0/80 | 0/10 | 0/7 | 0/3 | 0 | 0/80 | 0/76 |
| qwen3.8-flash | 10/10 | 9/10 | 71/80 | 8/10 | 6/7 | 3/3 | 0 | 72/80 | 68/76 |
| gemini-3.8-flash | 10/0 | 0/10 | 0/80 | 0/10 | 0/7 | 0/3 | 0 | 0/80 | 0/76 |

Completed means an HTTP 2xx response containing final model text; schema success is separate. Failures receive zero credit under the frozen protocol. Zero wrong-confident assignments for Gemini is unobserved model behavior, not safety evidence. Qwen’s parsed outputs contain 76/76 verbatim evidence snippets, including h01. h01 still receives zero schema/field/grounding credit. Of these, 68/68 snippets are in schema-valid outputs. The literal-snippet hard gate is audited even on parsed schema-invalid JSON; the frozen scoring pipeline remains strict.

| Candidate | p50 ms | p95 ms* | Min/max ms | Total tokens | Average tokens per exposed call | Provider failures | Output failures |
|---|---|---|---|---|---|---|---|
| gemini-3.5-flash-lite | 343.00 | 377.79 | 322.88/377.79 | None | None | {"PROVIDER_4XX": 10} | {"PROVIDER_4XX": 10} |
| qwen3.8-flash | 7626.54 | 8955.06 | 5690.24/8955.06 | 18702 | 1870.2 | {} | {"SCHEMA_INVALID": 1, "SEMANTIC_WRONG": 1} |
| gemini-3.8-flash | 345.83 | 388.28 | 320.40/388.28 | None | None | {"PROVIDER_4XX": 10} | {"PROVIDER_4XX": 10} |

*Nearest-rank p95 equals the maximum with ten observations; this is descriptive, not a reliable population tail estimate. Wall latency covers HTTP request/response and excludes local scoring. Null tokens mean unexposed, never zero. Qwen exposed 14,278 input and 4,424 output tokens (averages 1,427.8 and 442.4); no separate reasoning usage was exposed.

## Required fixtures

| Candidate | Fixture | HTTP | Schema | Fields /8 | Proposed SKU | Review success | Core status | Wrong confident | Evidence grounded | Errors |
|---|---|---|---|---|---|---|---|---|---|---|
| gemini-3.5-flash-lite | h05_no_size | 400 | False | 0 | None | False | None | False | False | PROVIDER_4XX |
| gemini-3.5-flash-lite | h06_no_pack | 400 | False | 0 | None | False | None | False | False | PROVIDER_4XX |
| gemini-3.5-flash-lite | h07_not_medium | 400 | False | 0 | None | False | None | False | False | PROVIDER_4XX |
| gemini-3.5-flash-lite | h08_not_large | 400 | False | 0 | None | False | None | False | False | PROVIDER_4XX |
| gemini-3.5-flash-lite | h09_latex | 400 | False | 0 | None | False | None | False | False | PROVIDER_4XX |
| qwen3.8-flash | h05_no_size | 200 | True | 7 | None | True | HUMAN_REVIEW | False | True | SEMANTIC_WRONG |
| qwen3.8-flash | h06_no_pack | 200 | True | 8 | None | True | HUMAN_REVIEW | False | True | none |
| qwen3.8-flash | h07_not_medium | 200 | True | 8 | GLOVE-N-L | False | CLEAN_DRAFT | False | True | none |
| qwen3.8-flash | h08_not_large | 200 | True | 8 | GLOVE-N-M | False | CLEAN_DRAFT | False | True | none |
| qwen3.8-flash | h09_latex | 200 | True | 8 | None | True | HUMAN_REVIEW | False | True | none |
| gemini-3.8-flash | h05_no_size | 400 | False | 0 | None | False | None | False | False | PROVIDER_4XX |
| gemini-3.8-flash | h06_no_pack | 400 | False | 0 | None | False | None | False | False | PROVIDER_4XX |
| gemini-3.8-flash | h07_not_medium | 400 | False | 0 | None | False | None | False | False | PROVIDER_4XX |
| gemini-3.8-flash | h08_not_large | 400 | False | 0 | None | False | None | False | False | PROVIDER_4XX |
| gemini-3.8-flash | h09_latex | 400 | False | 0 | None | False | None | False | False | PROVIDER_4XX |

## Hard gates

| Gate | Gemini 3.5 Flash-Lite | Qwen 3.8 Flash | Gemini 3.8 Flash |
|---|---|---|---|
| zero_wrong_confident_skus | PASS | PASS | PASS |
| h05_h06_h09_successful_review | FAIL | PASS | FAIL |
| h07_h08_correct_distinguishing_sku | FAIL | PASS | FAIL |
| zero_ambiguous_clean_promotions | PASS | PASS | PASS |
| every_returned_snippet_verbatim | UNOBSERVED | PASS | UNOBSERVED |
| all_mandatory_fields_match | FAIL | FAIL | FAIL |

## Method and provenance

All frozen inputs and schema hashes match before and after the batch. Exact original core/scorer Git blobs from 12a7e50 were copied under frozen_reference because they are absent from this branch. Schema, 0.90 threshold, 0.15 margin, strict expected-value scoring, and field/evidence denominators remain unchanged. Every returned line is inspected for evidence and wrong-confident proposals before later core rejection.

The explicit Phase-1 instruction overrides the historical CLI provider and repeat settings: candidate-major h01–h10 once each, with fresh direct API requests. Only the frozen synthetic prompt/catalog/source are sent; labels and previous outputs are excluded. Gemini uses documented responseFormat.text schema mode; Qwen uses Singapore-supported JSON Object mode, receives the identical schema as a format instruction, and uses the same strict local validator. No local schema weakening or output repair occurs.

The user-authorized grounding fix allows ordinary terminal sentence punctuation while retaining literal quote containment and word/decimal boundaries. Description comparison still preserves punctuation. The copied deterministic core performs arithmetic/contract checks locally to reproduce expected statuses; these decisions are never delegated to AI. No product runtime or approval is implemented.

Free-only settings rely on the preflight user confirmation; remaining quota is unknown. Qwen’s corrected endpoint was loaded from Windows user scope because the inherited process value was stale. No secret or endpoint values were printed/persisted. The 180-second diagnostic socket timeout does not establish the product’s 4.5-second timeout/5-second failure requirement. Ten shared fixtures and self-reported confidence do not establish broad reliability.

Artifacts: phase1_calls.json retains raw final model text, configurations, outcomes, scores, evidence checks, latency and usage; phase1_summary.json contains denominators, per-field/per-fixture metrics, gates and distributions; phase1_metadata.json records hashes/provenance/audit. Preflight artifacts are byte-identical.

Checks: python -B -m unittest discover -s spikes/ordershield/provider_bakeoff -p "test_*.py" -q (28 passed); python -B spikes/ordershield/provider_bakeoff/phase1.py validate (PASS); git diff --check (PASS); all 30 call identities, local score reproduction, aggregate reproduction, frozen/preflight hashes, secret exclusion and whitespace (PASS).

Provider references: [Gemini structured output](https://ai.google.dev/gemini-api/docs/generate-content/structured-output), [Qwen Singapore JSON Object / JSON Schema exclusion](https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output).
