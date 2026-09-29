# OrderShield: disposable feasibility spike

**REPLAY ONLY. Synthetic data. Live AI reliability UNVERIFIED.**

This is a small experiment, not the product. Python 3.11+; no installation,
dependencies, API credential, database, server or network connection required.

From the repository root:

```powershell
python spikes/ordershield/ordershield.py
python -m unittest discover -s spikes/ordershield -p 'test_*.py' -v
python spikes/ordershield/evaluate.py --repeat 20
```

One order, with JSON saved for inspection:

```powershell
python spikes/ordershield/ordershield.py --input spikes/ordershield/fixtures/03_ambiguous.txt --output spikes/ordershield/local-results/ambiguous.json
```

Simulate unavailable AI output (native exit code **2**, intentional):

```powershell
python spikes/ordershield/ordershield.py --simulate-unavailable
```

The default run emits five draft reconciliation records. `CLEAN_DRAFT` means
the supplied model proposal passed this experiment's checks; it is not an order
approval or proof of correct extraction. Exit 0 includes expected business
violations/review cases; exit 2 means unavailable/invalid AI output.

## What runs

Text PO + matching curated recording -> shape/source evidence checks -> SKU
confidence/margin gate -> decimal arithmetic + synthetic contract/tier checks
-> JSON draft or explicit review/violation/unavailable state.

`recordings/` contains **curated AI-shaped outputs authored by the coding
assistant in this session**, not independent inference API captures. The user
explicitly selected this replay path. Nothing in the CLI performs fresh AI
extraction or semantic inference. New or edited text needs a matching recording;
hash checks prevent accidentally using old outputs for changed inputs, catalog,
prompt or provisional response schema. These hashes detect staleness; they are
not an authenticity or security mechanism. Replay files are selected by filename
unless `--recording PATH` is provided together with `--input PATH`.

`prompt.txt` and `ordershield.py:SCHEMA` describe a future extraction/matching
request. The JSON schema and output structure are local experimental artifacts,
not accepted public contracts. `expected.json` contains manually authored checks
from the synthetic documents/business assumptions and is never loaded by the
reconciler. A passing comparison tests replay fidelity, not AI accuracy.

Amounts are decimal strings in JSON; uncertain totals are null, never zero.
Scores are uncalibrated and must not be presented as measured accuracy.
No action places, approves, exports to ERP, or sends an order.

Read [REPORT.md](REPORT.md) for results, commands, limitations and demo verdict;
[spec.md](spec.md) for assumptions; [results/demo.json](results/demo.json) for all
structured output, source quotes and match candidates.
