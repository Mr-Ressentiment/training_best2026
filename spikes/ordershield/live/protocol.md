# Frozen scoring protocol

No demonstrations, previous responses or reference answers are sent to inference.
All ten inputs contain exactly one intended order line. Same four-product catalog,
unchanged prompt/schema/core and 0.90 / 0.15 decision rule as the replay spike.
Ten new orders, three independent sessions each; round-major order h01..h10,
then repeat twice. No application retries, response repair or replay fallback.

## Denominators and strictness

- **Schema-valid**: complete parsed JSON satisfying the existing SCHEMA, divided
  by all 30 scheduled attempts. Also give observed-call denominator if blocked.
- **Field extraction**: eight expected values per call (four header, four line),
  exact string comparison after whitespace collapsing only, preserving case,
  punctuation, numbers and null. Report per-field and whole-order accuracy.
  Missing/invalid/failed output earns zero; extra or missing lines fails the
  whole-order criterion. Report strict description mismatches separately so
  boundary/punctuation disagreements are not confused with wrong business data.
- **Correct SKU**: existing core's proposed SKU equals reference for seven
  determinate inputs (21 calls), one line only. A conservative abstention is not
  counted as a correct resolved match. Also report correct abstention separately.
- **Ambiguous-to-review**: h05 and h06 produce successful core HUMAN_REVIEW and
  no proposed SKU (6 calls). Do not count transport/schema/grounding failures as
  successful ambiguity routing. h09 material mismatch is separately reported.
- **Ambiguous-to-clean**: core CLEAN_DRAFT on h05/h06, divided by six; also report
  this broader error across all three expected-SKU-review cases (nine calls).
- **Wrong-confident SKU**: proposed SKU passes unchanged score/margin/ambiguity
  policy but differs from reference, including any proposed SKU on h05/h06/h09.
  Count proposal errors even if evidence/number validation later rejects the
  order. Report count/all 30 and count/confident proposals. For dangerous cases,
  additionally report count/9 attempts and whether the core actually said clean.
- **Provenance**: per expected field, non-null value has non-empty literal quote
  present in source and passes the core's grounded check. Correctly missing null
  with empty quote is valid missingness, not positive source evidence. Report
  non-null evidence coverage (76 fields x 3 = 228) and all-field evidence/null
  validity (240), plus whole-order coverage and CLI session provenance.
- **Refusal/malformed**: distinguish provider error/timeout, missing final,
  JSON parse error, schema rejection, source/numeric/core rejection and valid
  review. Preserve original final text. Detect refusal from explicit provider
  events; non-JSON final text is retained for human inspection, not guessed away.
- **Latency**: wall clock around each CLI subprocess, including login/startup,
  provider and process shutdown; do not claim model-only latency. Report every
  call and min, median, mean, nearest-rank p90/p95, max; failures/timeouts remain
  in overall distribution and successes receive a separate distribution.

Metrics for unattempted calls are null/unmeasured, never inferred from replay.
Do not accept product concept based on this experiment. The confidence numbers
are self-reported scores, not calibrated probabilities. Repeated calls on ten
inputs are correlated and do not make a broad 30-document validation set.

## Provider isolation

Use the existing ChatGPT-authenticated Codex CLI with the configured
`gpt-6-astra` / `high` settings pinned per call. Ignore unrelated user config
while retaining normal CLI authentication. Each call uses an empty temporary
working directory, a copied schema, stdin prompt, no session resume and an
ephemeral session. Disable tools, apps/plugins, hooks, memory and web search.
Do not read/copy credentials. Record command flags, CLI version, session ID,
usage, final output and emitted tool events. Any tool event invalidates the
isolation claim and is an explicit contaminated-call failure.

Per-call timeout: 180 seconds. Stop remaining calls on authentication failure
as requested by the user. Unexpected CLI setup failure on the first call also
stops the batch. Do not relabel a launched-but-rejected request as completed AI
inference. CLI/provider-internal transport retries, if any, are observable only
to the extent the event stream reports them; this is not a raw API benchmark.

## Frozen-set limitations

Descriptions are new relative to the five replay fixtures; customer identifiers
and catalog attributes necessarily overlap. h02 deliberately copies a catalog
description, not a previous order. h07/h08 contrast medium/large with negations:
both SKUs cost EUR 8.00, use the same box unit and are allowed for Cedar Care,
so the numeric core cannot distinguish a wrong size. h09 requests latex, which
the catalog cannot supply. h10 is damaged text, not an OCR experiment.

The benchmark author knows the model family and prior replay findings; the
model calls receive none of that authoring context. Results are evidence for
human review, not an independent blind external evaluation.
