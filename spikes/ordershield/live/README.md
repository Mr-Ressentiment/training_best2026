# Live inference spike

Ten new held-out synthetic POs; three fresh CLI sessions per input. No product,
new dependency, API keys, replay fallback, or modifications to the original core.

The existing Codex CLI login must report `Logged in using ChatGPT`:

```powershell
codex login status
```

Provider mechanism: Codex CLI non-interactive `exec --json --output-schema`,
using saved authentication. See the official [non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode).
Configured model/effort are pinned in `adapter.py`. CLI configuration unrelated
to authentication is ignored for isolation. Model responses are requested in
fresh, ephemeral, read-only temporary workspaces with tools/memory/plugins off.
No credential files are read or copied by this experiment.

## Reproduce verification without spending inference calls

From `C:\training_best2026`:

```powershell
python -m unittest discover -s spikes/ordershield/live -p 'test_*.py' -v
python -m unittest discover -s spikes/ordershield -p 'test_*.py' -v
python spikes/ordershield/live/run.py check-freeze
python spikes/ordershield/live/audit.py
```

`audit.py` only reads recorded **real live call evidence** and recomputes metrics.
It is offline analysis, not replacement inference or reuse of the earlier replay
spike. It never changes raw calls, inputs, prompt, expected labels or frozen code.

## Executing the original experiment

The commands used before/during inference are:

```powershell
python spikes/ordershield/live/run.py freeze
python spikes/ordershield/live/run.py check-freeze
python spikes/ordershield/live/run.py run
```

The freeze and inference commands intentionally refuse an existing manifest or
results directory, preventing accidental overwrite/selective reruns. For a new
experiment, preserve this experiment and establish a separately reviewed cohort;
do not delete evidence or rebrand these inputs as newly held out.

`freeze.json` records SHA-256 for 19 artifacts (10 orders, reference labels,
protocol, runner, adapter, scorer, tests, original core/catalog/prompt). The schema
hash is recorded separately. `results/run_metadata.json` binds the actual run to
the manifest and CLI version. `results/calls/` retains all original final outputs,
CLI events excluding private reasoning, stderr warnings, session IDs, usage,
commands, timings, original scores and request hashes.

## Disclosed harness correction

The original event parser mistook CLI `error` items for tool events. The first
call exposed two startup notices (experimental skill-discovery setting and
disabled Code Mode host), not an actual tool execution. The frozen runner is
preserved unchanged, including its misleading CONTAMINATED_TOOL_USE labels.

`audit.py` removes only `error`/`warning` item types from that tool-event list,
then reuses the frozen classifier and scorer with the exact original outputs.
Actual and unknown tool types remain failures. `results/audited_scores.json`
contains original-to-corrected outcomes and raw-file hashes;
`results/audited_summary.json` is the corrected measurement. Tests exercise this
correction without model calls. Do not use the erroneous original summary as
the model-quality estimate. This is a transparent instrumentation correction,
not changed labels, response repair, selective sample removal or tuning.

Read `protocol.md` for pre-inference metric definitions and `REPORT.md` for the
observations and Human Gate limitations. No concept decision is made here.
