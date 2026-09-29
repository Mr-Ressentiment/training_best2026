# OrderShield live inference evidence for Human Gate

2026-09-29; branch `spike/ordershield-feasibility`; starting HEAD
`494a6bb6d478e7095fed06f4b43ab05d6c8f2872`.

**No concept acceptance. Semantic results are promising on this small set, but
the current live critical path is not established as demo-reliable.**

Thirty scheduled live CLI attempts were executed, three per new input. There
were **23 completed model outputs, three deterministic-core rejections among
those outputs, and seven provider usage-limit failures**. Thus only **20/30
attempts (66.7%)** produced a usable reconciliation result. The seven missing
third-pass outputs remain unmeasured; they are not successful inference calls.
No replay data, replacement model, response repair or inference retries were used.

## What was held fixed

- Ten new synthetic order inputs and expected answers were authored before calls.
  No complete description was copied from the five replay fixtures; h02 uses a
  catalog description deliberately. Existing customer/catalog identifiers remain.
- `freeze.json` was created at **2026-09-29 14:00:04.670616 UTC**, before the
  first call. Its 19 SHA-256 entries cover all inputs, reference labels, protocol,
  provider adapter, runner, scorer, original offline tests and original core /
  catalog / prompt. The original schema hash is also retained.
- Frozen artifacts were checked before every call and after the run. None changed.
- The entire original `spikes/ordershield/` implementation, fixtures, replay
  outputs, tests, catalog, prompt and thresholds are unchanged. New files are
  confined to its `live/` subdirectory.
- The evaluator's expected answers were never passed to inference. Each CLI call
  used an isolated empty temporary directory, one input, the unchanged prompt /
  catalog / schema, no prior session, no examples, and no prior outputs.

## Actual model and provenance

`codex login status` returned **Logged in using ChatGPT**. Existing login used;
no API keys, token files, secrets or login changes were requested or read.

CLI: `codex-cli 0.158.0-alpha.2.1`. Requested model: **gpt-6-astra**;
reasoning effort: **high**, matching the existing configuration. Calls used
non-interactive `exec --json --output-schema --ephemeral`, read-only mode,
unrelated user config ignored, tools/apps/plugins/memory disabled. Authentication
remained the CLI's normal saved login. This invocation mechanism is documented
in the official [non-interactive guide](https://learn.chatgpt.com/docs/non-interactive-mode).

Thirty unique CLI session IDs and exact invocation flags are recorded in
`results/calls/`. Every call has UTC start time, wall latency, request hash, exit
code, emitted events, stderr, exact final text if any, and usage when available.
Private reasoning events are excluded from export. There were **zero actual
tool-execution events**. Backend model revision is not exposed by this CLI event
stream; the evidence records the requested model, not an independently attested
immutable backend revision.

## Metrics

Primary definitions were frozen in `protocol.md`. Failed provider calls earn
no extraction/decision success; they must not be interpreted as evidence of
correct abstention or absence of semantic errors. Conditional rates below show
what happened only when an actual model response was available.

| Measure | All scheduled relevant attempts | Returned-output interpretation |
|---|---:|---|
| Schema-valid output | 23/30 = 76.7% | 23/23 returned responses valid under the unchanged schema |
| Correct extracted fields | 184/240 = 76.7% | 184/184 expected field values correct; whitespace-only normalization |
| Entire order's eight fields correct | 23/30 = 76.7% | 23/23 returned orders; no strict-description mismatches |
| Correct resolved SKU on determinate cases | 17/21 = 81.0% | 17/17 returned determinate cases |
| Correct abstention on all SKU-review cases | 6/9 = 66.7% | 6/6 returned missing-size, missing-pack and latex cases |
| Ambiguous-to-review, h05/h06 | 4/6 = 66.7% | 4/4 returned ambiguous cases; two provider failures |
| Ambiguous-to-clean, h05/h06 | 0 observed / 6 scheduled | 0/4 observed responses; two unobserved, not safe negatives |
| All SKU-review cases incorrectly clean | 0 observed / 9 scheduled | 0/6 returned review cases; three unobserved |
| Wrong-confident SKU errors | 0 observed / 30 scheduled | 0/17 confident proposals; seven calls returned no proposal |
| Dangerous wrong-confident errors, h07-h09 | 0 observed / 9 scheduled | 0/6 returned dangerous cases; three quota failures |
| Non-null source evidence accepted by core | 170/228 = 74.6% | 170/176 non-null fields in returned outputs |
| Provenance or correct null missingness | 178/240 = 74.2% | 178/184 returned fields; six quote-boundary rejections |
| All fields pass provenance check | 20/30 = 66.7% | 20/23 returned orders |
| CLI session provenance | 30/30 | Unique session ID and per-call metadata for every attempt |

Supplementary literal-quote inspection: **176/176 non-null returned fields have
non-empty quotes literally present in their input**. Six of these valid source
spans nevertheless fail the original core's value-boundary rule. Availability of
a source quote and acceptance by that deterministic rule are distinct measures.
Source presence does not prove the semantic association or completeness in
general; exact fixture ground truth checks these particular orders.

## Per-input results and call latency

`Q` means quota rejection, not a model refusal or an ambiguous SKU. `R` means
the unchanged core rejected otherwise schema-valid output. Durations are seconds,
including CLI startup, provider work and process shutdown, in repetition order.

| Input | Expected decision | Returned results r1 / r2 / r3 | Latency r1 / r2 / r3 |
|---|---|---|---|
| h01 PAPER-A4-80, explicitly named; 7 reams x 5.00 | PAPER-A4-80 | Clean / Clean / Clean | 16.357 / 17.207 / 14.222 |
| h02 catalog large nitrile gloves; 6 boxes x 8.00 | GLOVE-N-L | Clean / Clean / Clean | 11.949 / 11.259 / 11.793 |
| h03 medium nitrile handwear alias; 8 boxes x 8.00 | GLOVE-N-M | Correct proposal, R / R / R | 11.503 / 14.152 / 15.276 |
| h04 see-through PP sealing tape; 12 rolls x 1.80 | TAPE-48 | Clean / Clean / Q | 13.297 / 11.204 / 3.127 |
| h05 glove size omitted; 5 boxes x 8.00 | No SKU; review | Review / Review / Q | 16.558 / 17.617 / 3.414 |
| h06 medium glove pack count omitted; 2 boxes x 8.00 | No SKU; review | Review / Review / Q | 14.084 / 14.215 / 3.291 |
| h07 LARGE, NOT medium; 2 boxes x 8.00 | GLOVE-N-L | Clean / Clean / Q | 13.348 / 16.015 / 3.768 |
| h08 MEDIUM, NOT large; 3 boxes x 8.00 | GLOVE-N-M | Clean / Clean / Q | 11.630 / 16.055 / 3.601 |
| h09 latex, nitrile prohibited; 4 boxes x 8.00 | No catalog SKU; review | Review / Review / Q | 11.693 / 12.309 / 2.944 |
| h10 damaged form; buyer, quantity, price, total absent | PAPER-A4-80 plus missing-field review | Review / Review / Q | 13.896 / 17.798 / 2.832 |

Both dangerous size cases use the same EUR 8.00 price, box unit and allowed
customer contract for medium and large. Correctness therefore depends on the
semantic proposal, not arithmetic. All four returned size-trap responses chose
the requested size. The separate latex trap abstained in both returned responses.

| CLI latency distribution (seconds) | N | Min | Median | Mean | p90 | p95 | Max |
|---|---:|---:|---:|---:|---:|---:|---:|
| All attempts, including quota failures | 30 | 2.832 | 12.803 | 11.547 | 16.558 | 17.617 | 17.798 |
| Completed model outputs | 23 | 11.204 | 14.084 | 14.062 | 17.207 | 17.617 | 17.798 |
| Usable core results | 20 | 11.204 | 13.990 | 14.125 | 17.207 | 17.617 | 17.798 |
| Provider quota rejections | 7 | 2.832 | 3.291 | 3.282 | 3.768 | 3.768 | 3.768 |

Percentiles use nearest rank. Sum of measured CLI durations: **346.414 seconds**.
These are single sequential local-run measurements, not pure model latency or
a load test. Fast quota failures reduce the overall median; use the completed-
output row when estimating narration timing.

## Observed failures

**1. Unchanged core rejects sentence-final punctuation in source quotes.**
All three h03 outputs extract the correct values and medium SKU. They fail
`grounded()` because its trailing boundary disallows a period immediately after the value.
For example, value `box` with literal quote `Selling unit: box.` is rejected,
while quote `box` alone succeeds. Numeric sentence-final values encounter the
same issue: `8.00.` or `64.00.`. Across those three outputs, six individual fields
fail this boundary rule. The core stops at the first failure in each call.

This is an integration/core-validation limitation, not evidence that the model
invented a quote. The prompt did not prohibit quoting sentence punctuation.
The original core was deliberately left unchanged. The post-freeze diagnostic
test in `test_audit.py` reproduces the exact limitation without changing scoring
or fixture labels. Quotes and confidence/explanation wording vary across calls;
the expected extracted values and available SKU decisions remained stable here.

**2. Account usage limit interrupted the third pass.**
Calls 24-30 (h04-h10, repetition 3) exited 1 with no final text and provider
`turn.failed` events reporting a usage limit. This is not an authentication
failure. No provider/API-key switch, account change, paid upgrade, silent retry
or replay substitution was attempted. No usable third response exists for those
seven inputs; three completed generations for every input were not achieved.

**Refusal/malformed behavior:** among 23 returned outputs there were zero
explicit refusals, zero JSON parse failures and zero schema failures. There were
no 180-second timeouts. The damaged input returned null for all four absent
fields, identified the paper SKU and routed to human review in both available
responses. This does not establish behavior for arbitrary malicious, scanned,
multilingual or structurally different documents. Quota failures are recorded
as provider errors, not mislabelled model refusals or malformed JSON.

## Instrumentation correction, fully disclosed

The frozen adapter's event classifier incorrectly treated any CLI item other
than agent messages/reasoning/refusal as a tool event. This version of the CLI
emits its two startup notices as `item.type = error`: experimental skill
discovery disabled, and Code Mode host disabled. Consequently the original
`results/summary.json` labels all attempts CONTAMINATED_TOOL_USE and cannot be
used as a valid model-quality estimate.

The first call exposed this bug. The runner, fixtures, labels, prompt, model
settings and all raw records were kept unchanged. The separately added
`audit.py` excludes only non-tool `error` and `warning` items from the tool-event
list, then applies the unchanged classifier/scorer to the same final text.
Actual/unknown tool kinds, provider failures, parse/schema failures and grounding
rejections remain failures. Zero actual tool events were observed. No failed
inference was replaced, and no model response or expected answer was repaired.

Corrected evidence: `results/audited_summary.json` and
`results/audited_scores.json`. Every audited score references the original call's
SHA-256, original outcome and excluded event types. The audit script's own hash
is in the summary. Supplemental literal-quote and conditional-latency analysis
is explicitly labelled as post-freeze descriptive inspection, not a changed
acceptance criterion. The initial frozen `complete: true` means **30 attempts
recorded**, not 30 completed generations; the corrected summary explicitly
records that distinction.

## Verification and commands

Executed from `C:\training_best2026`, Python 3.11.9:

```powershell
codex login status
codex exec --help
codex features list
codex --version
python -m unittest discover -s spikes/ordershield/live -p 'test_*.py' -v
python spikes/ordershield/live/run.py freeze
python spikes/ordershield/live/run.py check-freeze
python spikes/ordershield/live/run.py run
python -m unittest discover -s spikes/ordershield -p 'test_*.py' -v
python -m compileall -q spikes/ordershield
git diff --exit-code -- spikes/ordershield/ordershield.py spikes/ordershield/catalog.json spikes/ordershield/prompt.txt spikes/ordershield/test_ordershield.py
python -m unittest discover -s spikes/ordershield/live -p 'test_audit.py' -v
python spikes/ordershield/live/audit.py
```

The runner invoked the exact CLI argument vectors saved in each raw call;
temporary paths are represented as `<isolated_temp>`. Model input is
reconstructible from the frozen prompt/catalog and named fixture, and checked by
the stored request SHA-256. No manual editing of provider output occurred.

Before inference, all **15 frozen harness tests passed** in 0.052 s. The original
spike's **29 tests passed** in 0.138 s. The three additional audit/diagnostic tests
passed in 0.001 s. Freeze validation and the protected-core Git diff passed;
inference runner exit 0 means its 30-attempt schedule finished, despite seven
individual CLI exit-1 failures. Audit exit 0; syntax check passed. There is no
configured repository-wide lint/typecheck/build command to claim as executed.
Final combined live-spike suite: **18 tests passed in 0.054 s**, exit 0. Together
with the 29 unchanged original tests, **47 tests passed**. Final syntax, freeze,
raw-evidence hash and whitespace checks all passed. These offline harness tests
do not turn the seven failed live calls into successes.

Additional final checks executed:

```powershell
python -m unittest discover -s spikes/ordershield/live -p 'test_*.py' -v
python -m compileall -q spikes/ordershield/live
python spikes/ordershield/live/run.py check-freeze
git diff --exit-code
git diff --check
$spikeFiles = @(rg --files --hidden spikes/ordershield/live)
foreach ($spikeFile in $spikeFiles) {
    $spikeDiff = @(git diff --no-index --check -- /dev/null $spikeFile 2>&1)
    if ($spikeDiff.Count -gt 0 -or $LASTEXITCODE -gt 1) { throw "Whitespace check failed for ${spikeFile}: $spikeDiff" }
}
Write-Output "New-file whitespace checks passed: $($spikeFiles.Count) files"
git status --short --branch
```

Result: **58 new files pass whitespace checks**; tracked diff empty; Git status
shows only `?? spikes/ordershield/live/`. An independent Python SHA-256 check
verified all 30 raw call files against the audited summary, 30 unique sessions,
and zero actual tool events.

## Files created and scope

All 58 additions are under `spikes/ordershield/live/`:

- `spec.md`, `plan.md`, `tasks.md`, `protocol.md`, `README.md`, `REPORT.md`.
- Ten `fixtures/h01_paper.txt` through `fixtures/h10_damaged.txt` inputs.
- `expected.json` and `freeze.json`.
- `adapter.py`, `run.py`, `score.py`, `test_live.py`.
- Disclosed correction/diagnostic files `audit.py`, `test_audit.py`.
- `results/run_metadata.json`, original `results/summary.json`, and 30 raw
  `results/calls/<fixture>-r<repeat>.json` files.
- `results/audited_scores.json`, `results/audited_summary.json`.

Existing files, product gate, pricing rules and schema were not modified.
No application infrastructure, dependencies, keys, commits, pushes or concept
acceptance were introduced.

## Interpretation for Project Brain / Human Gate

Observed successful responses handled the exact cases, aliases, missing
attributes, deliberate wrong-size traps, incompatible material and missing
fields correctly. This is evidence that meaningful live semantic extraction is
possible with the configured model. It is stronger evidence than curated replay.

It is still a tiny model-assisted synthetic dataset with correlated repetitions,
one model/effort setting, a four-product catalog and schema-constrained output.
There are only two completed responses for seven inputs. Zero observed wrong
confident proposals on 17 proposals is not proof of semantic safety or calibrated
confidence. Actual PDFs/OCR, novel customers, large catalogs and real commercial
policies remain outside this spike.

For a 3-5 minute demo, completed call latency of roughly 11-18 seconds is
compatible with a small narrated workflow, but current availability and quote
validation prevent a reliable-live claim. The two unresolved issues for human
review are the unchanged core's punctuation handling and adequate model quota
to complete the missing observations / support the demo. Any fix or new
inference cohort should be separately authorized and preserve this evidence.
No decision is made to accept OrderShield as the product concept.
