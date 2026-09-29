# Disposable implementation plan

Keep all changes under `spikes/ordershield/` on the existing
`spike/ordershield-feasibility` branch. Use Python 3.11 standard library only.

1. Create five text fixtures, a tiny synthetic catalog/contracts file, independent
   expected business outcomes, and a model prompt / provisional response schema.
2. Implement one CLI with explicit replay mode. Bind replay
   to source, catalog, prompt and schema hashes; never silently fall back.
3. Validate shape, field evidence, numbers and candidate membership before
   deterministic reconciliation. Keep incomplete totals null and require review.
4. Test business boundaries and failure cases. Save JSON results and timing,
   then document what the evidence supports and the remaining live AI gate.

The user explicitly selected recorded output replay during execution. Do not
build a live adapter in this spike. The prompt and provisional JSON schema can
be used in a later live experiment. No important product architecture decision
is being adopted by this spike.

Verification: standard-library unittest suite, compileall, replay evaluation,
unavailable-output run, and Git whitespace checks including new files.
No repository-wide lint, typecheck, build, or verify command is configured.
