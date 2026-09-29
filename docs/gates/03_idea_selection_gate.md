# Idea Selection Gate

## Candidate Concept

### 1. Problem

Small-business and wholesale operations staff manually reconcile incoming
customer purchase orders against internal product catalogs and contract pricing.

Customer terminology, product names and document formats often differ from the
company's internal representations, requiring repetitive manual interpretation
before an order can safely be accepted.

### 2. Target User

Operations or order-entry coordinator at a small-to-medium wholesale business.

### 3. Existing Workflow

The operator reads an incoming purchase order, identifies the requested products,
maps customer descriptions to internal SKUs, checks quantities and prices against
contract terms, investigates discrepancies, and manually creates or rejects the
order.

### 4. Pain Point

The workflow mixes fuzzy interpretation with exact business rules.

Manual SKU matching is repetitive and error-prone, while pricing, quantities and
contract restrictions must be checked precisely.

### 5. Proposed Solution

OrderShield converts an unstructured customer purchase order into a verified
order draft.

AI extracts order information and resolves semantically different product
descriptions to catalog candidates. Deterministic software validates quantities,
pricing, totals and contract rules before a human approves the resulting action.

### 6. Why AI?

AI is used only where interpretation is genuinely fuzzy:

- extraction from differently formatted order documents;
- semantic mapping between customer terminology and internal catalog SKUs;
- identifying ambiguity and candidate alternatives.

Deterministic software handles:

- arithmetic;
- quantities;
- prices and price tiers;
- contract restrictions;
- totals;
- persistence and state transitions.

### 7. End-to-End Demo Scenario

Customer PO
→ structured extraction
→ semantic SKU matching
→ deterministic contract validation
→ discrepancy or uncertainty review
→ human approval
→ order record creation
→ saved-state verification

### 8. Measurable Improvement

The prototype will measure:

- manual steps replaced by the unified workflow;
- time required to process the prepared test orders;
- number of seeded discrepancies detected;
- number of ambiguous product mappings correctly routed to human review.

No unsupported commercial accuracy or savings claims will be made.

### 9. Main Technical Risk

The primary risk is stochastic AI extraction and semantic SKU matching,
particularly overconfident incorrect matches and live-provider availability.

Feasibility spikes showed strong semantic behavior on the controlled synthetic
set, but provider quota/availability and broader generalization remain
implementation risks.

### 10. Fallback Plan

The demo-critical deterministic reconciliation core remains independent from the
AI provider.

If live inference is unavailable, the system must fail explicitly rather than
invent results. Prepared synthetic digital inputs and reproducible fixtures are
maintained for verification and demo recovery.

The MVP remains limited to a small fixed catalog and digital text/PDF-like
purchase orders.

# Decision

- [x] Concept accepted
- [ ] Concept rejected
- [ ] Fundamental uncertainty requires a short spike