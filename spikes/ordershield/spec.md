# OrderShield feasibility spike

Authority: the user's 2026-09-29 spike request. Disposable experiment only;
this does not accept the concept, create an MVP contract, or change the idea gate.

Repository inputs read before implementation:
- `../../AGENTS.md`
- `../../.specify/memory/constitution.md`
- `../../docs/briefing/01_participant_brief.md` (requested `challenge.md`)
- `../../docs/briefing/02_evaluation_rubric.md`
- `../../docs/gates/03_idea_selection_gate.md`

The three requested unnumbered briefing/gate paths do not exist. The numbered
files are the available equivalents. There are no accepted OrderShield specs,
ADRs, runtime code, or configured verification commands in this checkout.

## Acceptance criteria

1. Accept five clearly synthetic UTF-8 purchase orders: table, email, PDF-like
   wrapped text, delimited export, and split delivery schedule.
2. Produce customer, PO number, descriptions, quantities, sale units and prices
   with literal source evidence; missing information must remain unknown.
3. Propose catalog candidates for differently worded equivalent items, retain
   uncertainty, and route ambiguity to human review.
4. Compute quantities, customer contract eligibility, aggregate-SKU volume tiers,
   price differences, and totals exclusively in deterministic decimal code.
5. Demonstrate clean, semantic, ambiguous, price violation, and split-line tier /
   total mismatch cases; emit JSON suitable for a later UI experiment.
6. Reject invalid model output and stale replay files; handle unavailable AI
   output, missing fields, unknown customers/SKUs, and bad numbers.
7. Report reproducible results and distinguish curated replay from measured AI
   inference. Do not infer live AI accuracy from passing replay tests.

## Explicit experimental assumptions (not product requirements)

- Small English-language catalog, EUR only, positive integer sale-unit quantities.
- Net prices, no tax/freight/discount stacking, exact-cent equality.
- Tier quantity sums all resolved lines of the same SKU in one PO.
- Contract is a synthetic customer/SKU allowlist plus catalog tier prices; no
  validity dates, negotiated overrides, or real commercial policy is claimed.
- Model scores are uncalibrated self-assessments. Experimental acceptance needs
  score >= 0.90 and top-two margin >= 0.15; ambiguity always routes to review.
- All outputs are reconciliation drafts and cannot place or approve an order.
- PDF-like means text as if copied from a PDF. PDF binary parsing/OCR is excluded.
- Local curated AI-shaped responses are an explicitly labelled fallback, not
  independent API captures or evidence that live extraction works.

Non-goals: product, authentication, UI, database, ERP, deployment, integrations
of any kind, infrastructure, or concept approval.
