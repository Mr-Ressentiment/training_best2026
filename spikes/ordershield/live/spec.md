# Live inference feasibility experiment

Authority: user's second spike request, 2026-09-29. Evidence for Project Brain /
Human Gate only. No concept acceptance, product scope, or infrastructure changes.

Acceptance: ten NEW synthetic text orders, each evaluated in three fresh live
inference sessions (30 attempts). Include clear cases, aliases, missing
attributes, ambiguous matches, two size-negation traps with identical catalog
prices/units, an incompatible material, and a damaged/incomplete order.

Keep the existing deterministic core, catalog, prompt, schema and thresholds
unchanged. Provider code belongs only in a small adapter. No replay imports,
fallbacks, post-failure fixture/prompt tuning, hidden retries or response repair.

Before the first held-out call, freeze input files, ground truth, prompt,
catalog, core/schema, adapter, evaluator and scoring protocol with SHA-256.
Expected answers must never enter model prompts or the inference workspace.
Use fresh sessions; capture exact final response, provider/session metadata,
timing, errors and deterministic result for every attempted call.

Report denominator-explicit schema validity, extraction correctness, SKU
correctness, ambiguity routing, ambiguous-to-clean, wrong-confident SKU errors,
source provenance, refusals/malformed/transport failures and latency distribution.
Freeze imperfections are findings, not permission to change the benchmark.

The dataset is held out from prompt development, but authored by this coding
assistant, not independent human annotators. Small synthetic sample; no claim of
statistical generalization, actual PDF/OCR reliability, or calibrated confidence.
