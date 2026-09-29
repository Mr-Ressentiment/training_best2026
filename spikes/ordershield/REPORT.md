# OrderShield feasibility spike report

Date: 2026-09-29. Branch: `spike/ordershield-feasibility`.
Starting HEAD: `fac341c`. All added files are under `spikes/ordershield/`.
No concept acceptance, commit, merge, product implementation, or existing-file
change is part of this experiment.

## Decision supported by this evidence

**Conditional yes for a clearly labelled 3-5 minute replay demo. Live AI
extraction and matching feasibility remains UNVERIFIED.**

The deterministic reconciliation path works for the five curated proposals,
uncertainty is visible, and unavailable/invalid proposals produce explicit
failure states. The experiment does not establish that an API can reliably
extract these documents, produce the demonstrated candidate scores, or handle
novel wording within demo latency. There were **zero independent model calls**.

The recorded outputs were authored by the coding assistant alongside the
fixtures, with explicit provenance. They are not captures of independently
executed inference, a held-out dataset, or proof of generalization. The user
explicitly selected replay and accepted leaving live API reliability unverified.
This limitation prevents a claim that the full live AI critical path is proven.

## Results for all five fixtures

Every fixture is synthetic. Four header fields and four fields per order line
match the authored expectations: **48/48 replay field comparisons per suite**.
Seven lines are retained; six have proposed SKUs and one needs human review.

| Fixture / format | Customer / PO | Extracted descriptions, quantities, prices | Matching result | Deterministic result |
|---|---|---|---|---|
| 01 clean / pipe table | Alder Office Supplies / PO-1001 | A4 copier paper 80 gsm, 500 sheets: 2 reams x EUR 5.00; transparent polypropylene tape 48 mm x 66 m: 3 rolls x EUR 2.00 | PAPER-A4-80 0.99; TAPE-48 0.99 | CLEAN_DRAFT; quoted, declared and contract total EUR 16.00 |
| 02 semantic / email | Cedar Care / PO-1002 | Powder-free blue examination gloves, nitrile, size M, 100 per box: 4 boxes x EUR 8.00 | GLOVE-N-M 0.96; alternative GLOVE-N-L 0.04; margin 0.92 | CLEAN_DRAFT; EUR 32.00. Examination/exam and M/medium equivalence is in the replay proposal |
| 03 ambiguous / wrapped PDF-like text | Cedar Care / PO-1003 | Blue nitrile examination gloves, 100 per box, size not specified: 3 boxes x EUR 8.00 | GLOVE-N-M 0.52 / GLOVE-N-L 0.48; margin 0.04; selected SKU null | HUMAN_REVIEW; quoted/declared EUR 24.00; contract total null; ask customer for size |
| 04 price violation / semicolon export | Alder Office Supplies / PO-1004 | A4 copier paper 80 gsm, 500 sheets: 10 reams x EUR 5.00 | PAPER-A4-80 0.99 | PRICE_RULE_VIOLATION; quantity 10 qualifies for EUR 4.50; quoted EUR 50.00 versus expected EUR 45.00; delta +EUR 5.00 |
| 05 split tier / delivery schedule | Harbor Logistics / PO-1005 | Clear packing tape 48 mm x 66 m: 6 rolls x EUR 1.80; transparent polypropylene tape 48 mm x 66 m: 4 rolls x EUR 1.80 | Both TAPE-48, scores 0.93 / 0.99 | Both line prices PASS at aggregate quantity 10; computed/contract EUR 18.00 versus declared EUR 19.00; TOTAL_MISMATCH (-EUR 1.00) |

Full literal descriptions, source quotes, candidate explanations, per-line
arithmetic, prices, tiers, issues and provenance are in `results/demo.json`.

The evaluator ran 20 five-order suites: **100/100 fixture runs passed, zero
failed**. Repetition checks deterministic replay consistency only, not 100 AI
trials. Observed final five-order batch timing: min 6.305 ms, median 6.500 ms,
max 14.884 ms. This includes local replay reads/validation/reconciliation;
it excludes interpreter startup, any AI inference and human review.

## Verification commands and results

Executed from `C:\training_best2026` with Python 3.11.9:

```powershell
python -m unittest discover -s spikes/ordershield -p 'test_*.py' -v
python spikes/ordershield/ordershield.py --output spikes/ordershield/results/demo.json
python spikes/ordershield/evaluate.py --repeat 20 --output spikes/ordershield/results/evaluation.json
python -m compileall -q spikes/ordershield
python spikes/ordershield/ordershield.py --simulate-unavailable --output spikes/ordershield/results/unavailable.json
```

- unittest: **29 tests passed in 0.171 s**, exit 0 in the final run. Includes
  the explicit wrong-size limitation test and subcases for all five orders,
  numeric boundaries, candidate thresholds, malformed/stale responses and more.
- Replay demo: exit 0; all five structured records produced.
- Evaluation: exit 0; 100 passed / 0 failed; saved JSON contains all eight
  expected checks per fixture and measured timing.
- compileall: exit 0, no diagnostics. This is a syntax check, not a typecheck.
- Unavailable-output simulation: five AI_UNAVAILABLE records, no totals or
  invented lines; expected native exit 2 (PowerShell execution wrapper reported
  a nonzero process result). See `results/unavailable.json`.
- There is no configured repository lint, typecheck, build or verification
  command to run. None is claimed as passed.

The unavailable simulation's native exit was separately confirmed with:

```powershell
python spikes/ordershield/ordershield.py --simulate-unavailable --output spikes/ordershield/results/unavailable.json
$spikeExit = $LASTEXITCODE
Write-Output "Native Python exit: $spikeExit (expected 2)"
if ($spikeExit -ne 2) { throw 'Unexpected unavailable simulation exit' }
$spikeUnavailable = Get-Content spikes/ordershield/results/unavailable.json -Raw | ConvertFrom-Json
$spikeUnavailable.results | Select-Object status,totals
if (@($spikeUnavailable.results | Where-Object { $_.status -ne 'AI_UNAVAILABLE' -or $null -ne $_.totals }).Count -gt 0) { throw 'Unexpected unavailable result' }
```

Result: native exit 2, five AI_UNAVAILABLE / null totals, assertion wrapper exit 0.

Final Git checks (PowerShell statements shown on separate lines for readability):

```powershell
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Tracked whitespace check failed' }
$spikeFiles = @(rg --files --hidden spikes/ordershield -g '!__pycache__/**' -g '!local-results/**')
foreach ($spikeFile in $spikeFiles) {
    $spikeDiff = @(git diff --no-index --check -- /dev/null $spikeFile 2>&1)
    if ($spikeDiff.Count -gt 0 -or $LASTEXITCODE -gt 1) { throw "Whitespace check failed for ${spikeFile}: $spikeDiff" }
}
Write-Output "New-file whitespace checks passed: $($spikeFiles.Count) files"
git status --short --branch
```

Result: exit 0; **25 new files pass whitespace checks**. `git diff --name-only`
was empty; `git status --short --untracked-files=all` listed only the 25 spike
files. An earlier new-file check stopped on a CRLF-to-LF warning from generated
JSON. The eight generated JSON artifacts were normalized and both output writers
now explicitly use LF, matching `.gitattributes`; the full check then passed.

## Failure cases and what the checks do not prove

Verified tests cover:

- Missing quantities/prices/headers remain unknown and route to review; incomplete
  totals are null. Missing customer prevents contract validation.
- Unknown customers, disallowed customer/SKU combinations, wrong sale units and
  unsupported currency prevent a clean result. No implicit pack conversions.
- Negative/fractional/zero quantities, ambiguous number formats, excess decimal
  precision, non-finite numbers and invalid score types are rejected.
- Unknown or duplicate SKUs, empty candidates, close scores, ties, low scores,
  explicit ambiguity, unsupported quotes and invented fields fail or need review.
- Any unresolved SKU/unit/quantity defers all price tiers, since the unresolved
  line might change an aggregated SKU quantity.
- Changed source/catalog/prompt/schema invalidates a replay; missing/malformed
  or refusal-shaped output is explicit. No silent cached substitution occurs.
- Extra model approval/arithmetic fields are rejected. This is a limited output
  validation test, **not** a live prompt-injection resistance demonstration.

**Observed limitation:** injecting a 0.99-confidence large-glove proposal into
the medium-glove order still produces CLEAN_DRAFT. Both SKUs have the same price
and unit. `test_known_limit_confident_wrong_size_can_pass_numeric_rules`
demonstrates this fact. All drafts require human confirmation. Numeric checks
cannot establish semantic correctness; auto-approval is not justified.

Literal substring evidence establishes text presence only. It cannot prove
that the model associated a number with the correct line, included every line,
identified the correct buyer, or selected all plausible alternatives. Fixture
expectations detect omissions on these five cases, not on arbitrary new orders.
The clear-tape proposal also relies on a tiny catalog with only one tape entry;
it does not establish material equivalence in a larger catalog.

PDF-like inputs are UTF-8 text, not PDF binaries, scans, OCR, or layout extraction.
The fixture policies assume English, EUR, integer sale units, net totals without
tax/freight and one synthetic tier rule. No real contracts, customer discovery,
market differentiation, commercial savings or measured manual time baseline
have been validated.

## What remains stochastic

Replay and decimal reconciliation are deterministic apart from measured runtime.
Future live extraction may omit/misassociate fields, misread layout, choose the
wrong SKU, omit an alternative, or give an overconfident score. Refusals, response
formatting, service availability and inference latency are unmeasured. The 0.90
score / 0.15 margin thresholds are experimental, not calibrated probabilities.

## Proposed four-minute demonstration

1. 0:00-0:30: Explain the order-entry clerk hypothesis and label synthetic data /
   recorded AI outputs visibly.
2. 0:30-1:15: Show clean input -> replay extraction with quotes -> calculated draft.
3. 1:15-2:00: Show semantic glove wording -> catalog candidate and alternative.
4. 2:00-2:40: Show missing size -> no selected SKU -> human review.
5. 2:40-3:25: Show price-tier violation and split-line aggregate/total mismatch.
6. 3:25-4:00: Simulate unavailable AI output; explain draft-only boundary and the
   distinction between measured arithmetic and unverified live inference.

Timing is a proposed narration budget, not an observed user rehearsal. JSON is
the only output; there is no polished UI. Keep the unavailable-output command
separate, since its expected nonzero exit intentionally signals failure.

## Idea selection implication

The loop supports the challenge's structured report action, deterministic
verification, explicit uncertainty and human-control mechanisms. It targets
technical robustness and trustworthiness in the rubric. The meaningful live AI
component and an independently demonstrated end-to-end AI path remain open.
Do not check the idea-selection gate as accepted on replay evidence alone.

Before a live-demo commitment, the project lead should review a small fresh
inference experiment using a configured model, independently authored wording
and missing-field variants, repeated runs with measured latency, and manual
ground truth. Specifically measure wrong confident SKUs and ambiguous-to-clean
errors. This is a proposed follow-up decision, not new accepted product scope.

## Files created

All paths below are relative to `spikes/ordershield/`:

- `spec.md`, `plan.md`, `tasks.md`: bounded scope, assumptions and execution record.
- `README.md`, `REPORT.md`: reproducible usage and feasibility evidence.
- `ordershield.py`: schema, validation, replay loader, deterministic rules and CLI.
- `evaluate.py`, `test_ordershield.py`: replay evaluator and unit/failure tests.
- `catalog.json`, `expected.json`, `prompt.txt`: synthetic policy, expected
  outcomes and prospective extraction/matching instructions.
- `fixtures/01_clean.txt`, `fixtures/02_semantic.txt`,
  `fixtures/03_ambiguous.txt`, `fixtures/04_price_violation.txt`,
  `fixtures/05_split_tier.txt`: five unstructured synthetic inputs.
- `recordings/01_clean.json`, `recordings/02_semantic.json`,
  `recordings/03_ambiguous.json`, `recordings/04_price_violation.json`,
  `recordings/05_split_tier.json`: curated replay proposals with binding hashes.
- `results/demo.json`, `results/evaluation.json`, `results/unavailable.json`:
  executed-run artifacts.
- `.gitignore`: local bytecode and scratch-result exclusions.

Python created ignored `__pycache__/` bytecode during verification. No dependency
files or existing repository files were changed.
