# Feature Specification: OrderShield Purchase Order Reconciliation

**Feature Branch**: `001-ordershield-po-reconciliation`

**Created**: 2026-09-29

**Status**: Draft

**Input**: User description: "Create the product specification for the accepted OrderShield concept: converting unstructured customer purchase orders into verified order drafts through AI-driven extraction and semantic SKU matching combined with deterministic contract validation and human approval."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - End-to-End Clean Purchase Order Intake & Verification (Priority: P1)

An operations coordinator receives a digital customer purchase order document containing non-standard customer phrasing and layout. The operator submits the document into OrderShield. The system parses the document, extracts order metadata and line items, semantically maps customer product terminology to the internal catalog SKUs, and deterministically validates pricing and quantities against the customer's contracted terms. The operator reviews the populated draft with highlighted source evidence, confirms the verified line items, and approves the order, generating a committed order record.

**Why this priority**: This is the core critical path and primary value proposition of OrderShield. Without this complete intake-to-commit vertical slice, the product cannot demonstrate the unified "Understand → Reason → Act → Verify" workflow or establish baseline automation value.

**Independent Test**: Can be fully tested end-to-end by submitting a standard synthetic customer purchase order matching catalog items. The test verifies that the system extracts header metadata, maps line items to the correct SKUs, confirms pricing matches contract rates, presents the draft for operator confirmation, and upon approval persists a finalized order record.

**Acceptance Scenarios**:

1. **Given** a valid customer purchase order document with standard customer terminology and contracted pricing, **When** the operator submits the document for reconciliation, **Then** the system extracts the customer identifier, PO number, and line items, maps each line item to its correct catalog SKU, confirms line calculations and contract pricing without discrepancies, and presents a "Ready for Approval" draft.
2. **Given** a verified order draft in "Ready for Approval" status, **When** the operator clicks to approve the order, **Then** the system transitions the draft to "Approved", creates a finalized order record with an immutable verification timestamp, and retains full links to the extracted source data.

---

### User Story 2 - Automated Discrepancy Detection & Ambiguity Routing (Priority: P2)

An operations coordinator receives a purchase order containing discrepancies such as an out-of-date or incorrect unit price, an ambiguous product description that could refer to multiple catalog SKUs, or a quantity violating minimum order restrictions. The system extracts the order, flags each discrepancy with an explicit rationale, highlights the affected fields, and prevents premature approval. The operator inspects the highlighted issues, reviews candidate SKU options for the ambiguous description, selects the intended SKU, corrects mis-extracted fields or removes invalid lines, and either approves the compliant order or rejects the non-compliant draft.

**Why this priority**: Real-world purchase orders frequently contain errors, customer pricing assumptions, and vague descriptions. Ensuring that AI uncertainty and commercial discrepancies are explicitly flagged rather than silently hallucinated or auto-accepted is mandatory for trustworthiness and commercial safety.

**Independent Test**: Can be independently tested by submitting a synthetic purchase order seeded with an off-contract unit price and an ambiguous product description. The test verifies that the system flags the pricing discrepancy, refuses automatic clean approval, displays alternative candidate SKUs with relative match rationales, and enables operator correction or line removal before finalization.

**Acceptance Scenarios**:

1. **Given** an incoming purchase order where the customer states a price lower than their agreed contract rate, **When** the reconciliation engine validates the extracted draft, **Then** the system marks the line item with a pricing discrepancy flag, displays both the customer-requested price and the contract price, and blocks order approval while the contract-price violation persists, requiring the operator to correct any mis-extracted field, remove the non-compliant line, or reject the draft.
2. **Given** a line item with an ambiguous customer description matching multiple catalog products, **When** semantic SKU matching is performed, **Then** the system marks the mapping as uncertain, lists candidate catalog SKUs for operator selection, and prohibits finalized order creation until the operator selects an explicit SKU or removes the item.
3. **Given** a purchase order with critical unresolvable errors or invalid customer account data, **When** the operator reviews the flagged draft, **Then** the operator can reject the draft with a recorded reason, preventing order creation and archiving the intake record as rejected.

---

### User Story 3 - Traceable Grounding & Audit Trail Inspection (Priority: P3)

An operations supervisor or coordinator needs to audit how a purchase order was interpreted, verified, and approved. They open a processed order record to inspect the exact textual evidence extracted from the original purchase order document, the catalog SKU matching rationale, the deterministic contract check results (prices, minimum quantities, totals), and the identity and timestamp of human sign-off.

**Why this priority**: Operational trust requires that AI outputs are grounded in verifiable source evidence. When inquiries or audits arise regarding order inaccuracies or billing disputes, staff must be able to inspect the provenance of every extracted field and rule check.

**Independent Test**: Can be tested by opening an approved or rejected order in the audit inspection view and confirming that each line item links directly to its source text snippet from the ingested document, shows the contract price comparison, and displays the full audit event history.

**Acceptance Scenarios**:

1. **Given** an approved order record, **When** the operator views the line-item details, **Then** the system displays the original raw text snippet from the customer document alongside the normalized catalog SKU, standard price, and applied contract discount.
2. **Given** an order that underwent manual operator correction (such as manual SKU selection, correction of mis-extracted quantity, or line removal), **When** reviewing the audit history, **Then** the system shows the initial extracted value, the operator's adjustment, and the timestamp of approval.

---

### Edge Cases

- **Out-of-Catalog or Completely Unrecognized Items**: If a purchase order lists an item that has no reasonable semantic match in the internal catalog, the system MUST flag the item as "Unrecognized SKU", provide no false matches, and require operator manual mapping or line exclusion before approval.
- **Unit of Measure and Minimum Order Quantity (MOQ) Breaches**: If an order requests a quantity that does not meet the catalog item's minimum order requirement (e.g., ordering 3 units when MOQ is 10) or requests an incompatible unit of measure (e.g., individual units when only cases are sold), the system MUST flag an order restriction discrepancy.
- **Unreadable or Malformed Document Input**: If an uploaded document is empty, corrupted, or contains unparseable text, the system MUST immediately present an explicit, human-readable error indicating extraction failure and must not create a partial or corrupted draft.
- **Live AI Service Latency or Unavailability**: If the external AI service encounters a timeout, rate limit, or network disconnection during processing of a user-submitted document, the system MUST fail explicitly, presenting a clear error state indicating provider failure. The system MUST NOT silently substitute pre-recorded fixture data for a live user-submitted document. If the user explicitly launches or switches to fixture/replay mode (for offline demonstration or testing), the system interface MUST visibly and unambiguously label the session as non-live fixture replay.
- **Missing Mandatory Order Header Metadata**: If the customer document omits required fields such as the customer identifier or purchase order reference number, the system MUST mark the draft as incomplete and require operator input before contract validation can proceed.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept digital purchase order documents in standard digital document and text formats.
- **FR-002**: System MUST extract key order header fields from the ingested document required for reconciliation: customer identity/name and customer purchase order reference number.
- **FR-003**: System MUST extract all line items from the document, including customer-provided item description, requested quantity, customer-stated unit price, and stated line total.
- **FR-004**: System MUST maintain grounded textual provenance linking every extracted header and line-item field to the specific text or section in the source document.
- **FR-005**: System MUST semantically match extracted customer product descriptions against an internal catalog of standard SKUs and calculate a match confidence state (High Confidence, Ambiguous/Multiple Candidates, or Unrecognized).
- **FR-006**: System MUST present ranked candidate catalog SKUs for any line item classified as ambiguous or low-confidence, requiring operator selection before order finalization.
- **FR-007**: System MUST NOT guess or fabricate catalog SKUs when a customer description falls below acceptable confidence thresholds; it MUST mark the item as unrecognized.
- **FR-008**: System MUST deterministically calculate quantities, unit prices, line totals, contract/tier pricing, and grand totals using verified business arithmetic, without relying on AI for numerical computation.
- **FR-009**: System MUST deterministically validate customer-stated prices and quantities against the active customer contract terms, authorized price tiers, and minimum order quantities.
- **FR-010**: System MUST detect and categorize discrepancies strictly within the supported MVP categories:
  - Price discrepancy (customer-stated unit price != contracted/tier unit price);
  - Quantity/packaging discrepancy (requested quantity < minimum order quantity or non-standard packaging increment);
  - Arithmetic discrepancy (stated line total != quantity * unit price, or stated order total != sum of line totals);
  - Catalog matching discrepancy (unrecognized or ambiguous SKU).
- **FR-011**: System MUST present a unified reconciliation view that visually distinguishes verified fields, low-confidence mappings, and detected discrepancies.
- **FR-012**: System MUST permit the operator to correct extracted fields, select an explicit SKU from suggested candidates, remove invalid line items, or reject the draft, but MUST NOT support commercial price overrides in the MVP.
- **FR-013**: System MUST enforce mandatory human approval prior to converting any draft order into a finalized, committed order record.
- **FR-014**: System MUST allow the operator to reject an invalid or non-compliant purchase order with a recorded rejection reason.
- **FR-015**: System MUST persist verified order records, line items, reconciliation status, and operator actions in persistent storage.
- **FR-016**: System MUST maintain an immutable audit log capturing original ingested data, AI extraction outputs, discrepancy flags, operator adjustments, and final approval timestamps.
- **FR-017**: System MUST provide an explicit error state when document parsing or semantic matching fails or is unavailable, and MUST NOT silently substitute fixture data for live user-submitted documents. When operating in fixture or replay mode, the system MUST visibly label the session as non-live.

### Key Entities *(include if feature involves data)*

- **Purchase Order Document**: The original incoming digital document submitted for intake, including filename, raw text content, ingestion timestamp, and processing status.
- **Order Draft**: The working reconciliation entity representing an intake session. Contains order header attributes (customer identity, purchase order reference number), reference to the customer contract, draft status (`Ingested`, `Needs Review`, `Ready for Approval`, `Approved`, `Rejected`), and list of line drafts.
- **Draft Line Item**: An extracted order line containing original customer description, extracted quantity, customer unit price, grounded text snippet reference, matched catalog SKU, match confidence level, candidate SKU suggestions, validated contract price, calculated line total, and discrepancy flags.
- **Catalog Product (SKU)**: An item from the internal product master, including SKU code, standard product name, product category, unit of measure, base unit price, and minimum order quantity.
- **Customer Contract**: Commercial terms established with a specific customer, including customer identifier, contracted pricing rules/tiers per SKU, and contract validity window.
- **Discrepancy Flag**: A structured indicator associated with an order draft or line item detailing discrepancy type (strictly `PriceMismatch`, `QuantityOrPackagingBreach`, `ArithmeticMismatch`, or `CatalogMatchingMismatch`), severity level, expected value, requested value, and whether it has been resolved by field correction, line removal, or remains unresolved.
- **Verified Order Record**: The committed, finalized commercial order created upon explicit operator approval, containing immutable line items, approved prices and quantities, grand total, approving operator identity, and approval timestamp.
- **Audit Event**: An immutable log record capturing every lifecycle transition, field modification, operator adjustment (field corrections, SKU selections, line removals), draft rejection, and approval action.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001 (Prepared Demo Processing Velocity)**: An operations coordinator can complete the end-to-end reconciliation and approval of a prepared 5-line purchase order in under 60 seconds during the demo scenario.
- **SC-002 (Discrepancy Catch Rate)**: 100% of seeded pricing mismatches, arithmetic errors, and minimum order quantity violations in prepared test orders are detected and flagged prior to operator approval.
- **SC-003 (Zero Hallucinated Commitments)**: 100% of ambiguous or out-of-catalog customer descriptions are routed to operator review; zero unrecognized descriptions are automatically assigned to an unverified SKU.
- **SC-004 (Full Provenance Visibility)**: 100% of extracted line items provide visible textual grounding citations linking back to the source purchase order text.
- **SC-005 (Mandatory Gate Enforcement)**: 0% of unreviewed or discrepancy-laden order drafts can transition to a committed order record without explicit operator sign-off or resolution.
- **SC-006 (Explicit Failure & Replay Transparency)**: In the event of an unreadable document or AI provider failure, the system presents an explicit diagnostic error within 5 seconds without partial data corruption or silent fallback. When replay or fixture mode is active, 100% of views visibly indicate non-live demonstration status.

## Assumptions

- **Target Users & Context**: Primary users are wholesale operations and order-entry coordinators working via desktop web browsers who possess domain familiarity with catalog products and customer relationships.
- **Input Scope for MVP**: Inbound purchase orders are provided as digital text or machine-readable documents (such as digital PDFs or structured text files). Scanned physical image OCR, handwritten documents, and multi-currency conversions are explicitly out of scope for the MVP.
- **Catalog & Contract Fixtures**: A representative catalog dataset (10–30 wholesale products) and customer contract terms (with tiered pricing and MOQs) are pre-loaded to support deterministic validation and test fixtures.
- **Division of Responsibilities**: AI models are strictly constrained to fuzzy interpretation tasks (document field extraction, semantic SKU ranking, and ambiguity detection). All calculations, pricing lookups, rule enforcement, status transitions, and data storage are strictly deterministic.
- **Fallback Operations & Demo Modes**: To guarantee demo reliability in the event of external network or API provider latency/outages, the system supports reproducible fixture/replay modes using synthetic documents. Any replay or fixture execution is explicitly initiated and visibly badged as non-live, and never acts as a silent fallback for failed live user submissions.
