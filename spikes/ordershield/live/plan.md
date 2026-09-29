# Experiment plan

1. Inspect configured model without exposing credentials; identify the supported
   live invocation mechanism. No credential extraction or login changes.
2. Author fixtures, expected answers and metric definitions before inference.
3. Add provider adapter, append-only attempt runner, scorer and offline tests.
4. Freeze the experiment; execute each input three times with fresh contexts.
5. Score all attempts, retain failures, inspect dangerous cases, and report
   evidence and unresolved limitations. Verify original spike remains unchanged.

Use Python standard library and existing runtime only. No application server,
database, UI, integrations or additional dependency installation.
