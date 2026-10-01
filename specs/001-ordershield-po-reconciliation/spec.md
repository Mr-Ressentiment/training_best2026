# Feature Specification: OrderShield Purchase Order Reconciliation

**Feature Branch**: `001-ordershield-po-reconciliation`

**Created**: 2026-09-29

**Status**: Implemented & Verified (T041 GO)

**Input**: User description: "Create the product specification for the accepted OrderShield concept: converting unstructured customer purchase orders into verified order drafts through AI-driven extraction and semantic SKU matching combined with deterministic contract validation and human approval."

## Clarifications

### Session 2026-09-29

- Q: What exact digital file formats are mandatory for MVP intake, and what formats are explicitly excluded? → A: UTF-8 plain text files (`.txt`) and digital PDFs with extractable text streams (`.pdf`) MUST be supported. Scanned PDFs/images requiring OCR, image formats (`.png`, `.jpg`), spreadsheet files (`.xlsx`, `.csv`), and email containers (`.eml`) are explicitly excluded from the MVP. If an uploaded PDF has no extractable text stream, the system MUST reject it immediately with an explicit "unextractable text" error rather than attempting OCR.
- Q: Which extracted purchase order fields are strictly mandatory for the reconciliation critical path? → A: Mandatory header fields: Customer Identity/Name and Customer Purchase Order Reference Number. Optional header field: Customer-Stated Order Total (when explicitly present in the source purchase order, its provenance is mandatory and it is evaluated for order-level arithmetic independently from contract pricing). Mandatory line-item fields: Customer Product Description, Requested Quantity, Customer-Stated Unit Price, and Customer-Stated Line Total. All other document metadata (e.g., document dates, delivery dates, shipping addresses, payment terms, tax lines) are non-mandatory and are not evaluated by the reconciliation engine in the MVP.
- Q: Which specific conditions determine whether an Order Draft transitions to "Ready for Approval" versus "Needs Review"? → A: An Order Draft is `Ready for Approval` IF AND ONLY IF: (1) mandatory header fields are present, (2) all line items are resolved to valid catalog SKUs (either through High Confidence match or explicit operator selection), and (3) zero unresolved discrepancy flags exist across all lines and totals. If any line has an unmapped SKU, an unresolved price/quantity/arithmetic discrepancy, or missing mandatory header data, the draft status MUST remain `Needs Review`, and approval is blocked.
- Q: What explicit operator actions are permitted to resolve each type of detected discrepancy? → A: (1) Ambiguous SKU: Operator selects one candidate SKU from the ranked list or removes the line. (2) Unrecognized SKU: Operator manually selects a catalog SKU from the master, removes the line, or rejects the draft. (3) Incorrect Extraction: Operator edits the field value to match the source document, triggering automatic deterministic re-validation. (4) Pricing Discrepancy: Commercial price overrides are strictly prohibited; the operator may only correct a mis-extracted price to match the source text, remove the non-compliant line, or reject the draft. (5) Quantity/Packaging Breach: Operator may correct the extracted quantity ONLY when it does not match the source document; the operator MUST NOT alter a correctly extracted quantity merely to satisfy MOQ or packaging rules. If the discrepancy is valid against the source text, the operator may only remove the affected line or reject the draft. (6) Arithmetic Discrepancy: Deterministic arithmetic remains authoritative. If source values were mis-extracted, the operator may correct the extracted field to match the source document; otherwise the affected line must be removed or the draft rejected. An unresolved arithmetic mismatch MUST continue blocking Ready for Approval; the system does not permit accepting mismatched totals to clear the discrepancy.
- Q: What are the exact prerequisites and consequences of human approval and draft rejection? → A: Human approval is an explicit, irreversible operator action that requires the draft to be in `Ready for Approval` status. Approval creates an immutable `Verified Order Record` snapshotting the approved SKUs, verified quantities, contract rates, grand total, approving operator identity, and timestamp. A draft in `Needs Review` cannot be approved. An operator may explicitly reject a draft at any stage before approval with a mandatory recorded reason, transitioning the draft to `Rejected` and permanently preventing order creation.
- Q: How must the system isolate live AI inference from fixture/replay mode and handle service failures? → A: Fixture/replay mode MUST be explicitly initiated by the user for testing/demos and MUST visibly badge 100% of views with "DEMO / REPLAY MODE (NON-LIVE)". If live inference fails for a user-submitted document (timeout, quota limit, network error), the system MUST fail explicitly with a clear diagnostic error and MUST NEVER silently substitute fixture data for that submission.
- Q: What minimum grounding evidence must be captured and displayed for extracted fields and SKU matches? → A: For extracted text fields (header and line items): the exact verbatim text snippet from the raw document and a location pointer (line number, text offset, or block index). For semantic SKU matches: the input customer description snippet, the matched catalog SKU and standard product name, the match confidence level, and a candidate alternative list or matching rationale.
- Q: Which exact discrepancy categories are supported by the MVP reconciliation engine? → A: Strictly four categories: (1) `PriceMismatch` (customer-stated unit price != contracted/tier price), (2) `QuantityOrPackagingBreach` (requested quantity < minimum order quantity or non-standard packaging increment), (3) `ArithmeticMismatch` (stated line total != quantity * unit price, or stated order total != sum of line totals), and (4) `CatalogMatchingMismatch` (ambiguous or unrecognized SKU). All other commercial validation checks (credit limits, tax rates, address matching) are excluded from MVP.

### Session 2026-09-30 (Human Gate Reconciliation)

- Q: What are the runtime failure and timeout semantics for live AI inference under SC-006 following provider research? → A: (1) Immediately detectable failures (unreadable/local document failure, connection refusal, authentication failure, quota/rate-limit rejection, explicit upstream 4xx/5xx) MUST surface an explicit diagnostic error with a target of ≤5 seconds from request intake. (2) Healthy live inference executes within a bounded ≤15-second budget based on empirical bake-off latency. (3) Silent/stalled provider inference remains indistinguishable from slow healthy inference until the client deadline, where it MUST be aborted at ≤15 seconds and return an explicit diagnostic error with zero partial persistence and zero silent fallback. (4) Zero automatic provider failover or fixture substitution is permitted.

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

An operations coordinator receives a purchase order containing discrepancies such as an out-of-date or incorrect unit price, an ambiguous product description that could refer to multiple catalog SKUs, or a quantity violating minimum order restrictions. The system extracts the order, flags each discrepancy with an explicit rationale, highlights the affected fields, and prevents premature approval. The operator inspects the highlighted issues, reviews candidate SKU options for the ambiguous description, selects the intended SKU, corrects mis-extracted fields to match the document or removes invalid lines, and either approves the compliant order or rejects the non-compliant draft.

**Why this priority**: Real-world purchase orders frequently contain errors, customer pricing assumptions, and vague descriptions. Ensuring that AI uncertainty and commercial discrepancies are explicitly flagged rather than silently hallucinated or auto-accepted is mandatory for trustworthiness and commercial safety.

**Independent Test**: Can be independently tested by submitting a synthetic purchase order seeded with an off-contract unit price and an ambiguous product description. The test verifies that the system flags the pricing discrepancy, refuses automatic clean approval, displays alternative candidate SKUs with relative match rationales, and enables operator correction or line removal before finalization.

**Acceptance Scenarios**:

1. **Given** an incoming purchase order where the customer states a price lower than their agreed contract rate, **When** the reconciliation engine validates the extracted draft, **Then** the system marks the line item with a pricing discrepancy flag, displays both the customer-requested price and the contract price, and blocks order approval while the contract-price violation persists, requiring the operator to correct any mis-extracted field, remove the non-compliant line, or reject the draft.
2. **Given** a line item with an ambiguous customer description matching multiple catalog products, **When** semantic SKU matching is performed, **Then** the system marks the mapping as uncertain, lists candidate catalog SKUs for operator selection, and prohibits finalized order creation until the operator selects an explicit SKU or removes the item.
3. **Given** a purchase order with critical unresolvable errors or missing/unresolvable mandatory customer identity, **When** the operator reviews the flagged draft, **Then** the operator can reject the draft with a recorded reason, preventing order creation and archiving the intake record as rejected.

---

### User Story 3 - Traceable Grounding & Audit Trail Inspection (Priority: P3)

An operations supervisor or coordinator needs to audit how a purchase order was interpreted, verified, and approved. They open a processed order record to inspect the exact textual evidence extracted from the original purchase order document, the catalog SKU matching rationale, the deterministic contract check results (prices, minimum quantities, totals), and the identity and timestamp of human sign-off.

**Why this priority**: Operational trust requires that AI outputs are grounded in verifiable source evidence. When inquiries or audits arise regarding order inaccuracies or billing disputes, staff must be able to inspect the provenance of every extracted field and rule check.

**Independent Test**: Can be tested by opening an approved or rejected order in the audit inspection view and confirming that each line item links directly to its source text snippet from the ingested document, shows the contract price comparison, and displays the full audit event history.

**Acceptance Scenarios**:

1. **Given** an approved order record, **When** the operator views the line-item details, **Then** the system displays the original raw text snippet from the customer document alongside the normalized catalog SKU, standard price, and applied contract/tier price.
2. **Given** an order that underwent manual operator correction (such as manual SKU selection, correction of mis-extracted quantity to match the source document, or line removal), **When** reviewing the audit history, **Then** the system shows the initial extracted value, the operator's adjustment, and the timestamp of approval.

---

### Edge Cases

- **Out-of-Catalog or Completely Unrecognized Items**: If a purchase order lists an item that has no reasonable semantic match in the internal catalog, the system MUST flag the item as "Unrecognized SKU", provide no false matches, and require operator manual mapping or line exclusion before approval.
- **Unit of Measure and Minimum Order Quantity (MOQ) Breaches**: If an order requests a quantity that does not meet the catalog item's minimum order requirement (e.g., ordering 3 units when MOQ is 10) or requests an incompatible unit of measure (e.g., individual units when only cases are sold), the system MUST flag an order restriction discrepancy.
- **Unreadable or Malformed Document Input**: If an uploaded document is empty, corrupted, contains unparseable text, or is a PDF lacking an extractable text stream, the system MUST immediately present an explicit, human-readable error indicating extraction failure and must not create a partial or corrupted draft.
- **Live AI Service Latency or Unavailability**: If the external AI service encounters a timeout, rate limit, or network disconnection during processing of a user-submitted document, the system MUST fail explicitly, presenting a clear error state indicating provider failure. The system MUST NOT silently substitute pre-recorded fixture data for a live user-submitted document. If the user explicitly launches or switches to fixture/replay mode (for offline demonstration or testing), the system interface MUST visibly and unambiguously label the session as non-live fixture replay.
- **Missing Mandatory Order Header Metadata**: If the customer document omits required fields such as the customer identifier or purchase order reference number, the system MUST mark the draft as incomplete (`Needs Review`) and require operator input before contract validation can proceed.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept digital purchase order documents in UTF-8 plain text (`.txt`) and digital PDF with extractable text streams (`.pdf`). Scanned image PDFs, image files, spreadsheets, and emails are explicitly excluded from the MVP.
- **FR-002**: System MUST extract mandatory order header fields from the ingested document required for reconciliation: customer identity/name and customer purchase order reference number. System MAY extract an optional customer-stated order total when explicitly present in the source document, capturing mandatory provenance linking it to the source text.
- **FR-003**: System MUST extract all line items from the document, including customer-provided item description, requested quantity, customer-stated unit price, and stated line total.
- **FR-004**: System MUST maintain grounded textual provenance linking every extracted header and line-item field to the specific verbatim text snippet and document location in the source document.
- **FR-005**: System MUST semantically match extracted customer product descriptions against an internal catalog of standard SKUs and calculate a match confidence state (High Confidence, Ambiguous/Multiple Candidates, or Unrecognized).
- **FR-006**: System MUST present ranked candidate catalog SKUs for any line item classified as ambiguous or low-confidence, requiring operator selection before order finalization.
- **FR-007**: System MUST NOT guess or fabricate catalog SKUs when a customer description falls below acceptable confidence thresholds; it MUST mark the item as unrecognized.
- **FR-008**: System MUST deterministically calculate quantities, unit prices, line totals, contract/tier pricing, and grand totals using verified business arithmetic, without relying on AI for numerical computation.
- **FR-009**: System MUST deterministically validate customer-stated prices and quantities against the active customer contract terms, authorized price tiers, and minimum order quantities.
- **FR-010**: System MUST detect and categorize discrepancies strictly within the supported MVP categories:
  - Price discrepancy (`PriceMismatch`: customer-stated unit price != contracted/tier unit price);
  - Quantity/packaging discrepancy (`QuantityOrPackagingBreach`: requested quantity < minimum order quantity or non-standard packaging increment);
  - Arithmetic discrepancy (`ArithmeticMismatch`: stated line total != quantity * unit price, or stated order total != sum of customer-stated line totals, evaluated independently from contract pricing);
  - Catalog matching discrepancy (`CatalogMatchingMismatch`: unrecognized or ambiguous SKU).
- **FR-011**: System MUST present a unified reconciliation view that visually distinguishes verified fields, low-confidence mappings, and detected discrepancies.
- **FR-012**: System MUST permit the operator to correct extracted fields ONLY to match the source document, select an explicit SKU from suggested candidates, remove invalid line items, or reject the draft. The operator MUST NOT alter correctly extracted prices or quantities to bypass contract pricing, MOQ, or packaging rules, and the system MUST NOT support commercial price or quantity overrides in the MVP.
- **FR-013**: System MUST enforce mandatory human approval prior to converting any draft order into a finalized, committed order record, and MUST require that the draft be in `Ready for Approval` status before approval can be executed.
- **FR-014**: System MUST allow the operator to reject an invalid or non-compliant purchase order draft at any stage before approval with a mandatory recorded rejection reason.
- **FR-015**: System MUST persist verified order records, line items, reconciliation status, and operator actions in persistent storage.
- **FR-016**: System MUST maintain an immutable audit log capturing original ingested data, AI extraction outputs, discrepancy flags, operator adjustments, and final approval timestamps.
- **FR-017**: System MUST provide an explicit error state when document parsing or semantic matching fails or is unavailable, and MUST NOT silently substitute fixture data for live user-submitted documents. When operating in fixture or replay mode, the system MUST visibly label the session as non-live.

### Key Entities *(include if feature involves data)*

- **Purchase Order Document**: The original incoming digital document submitted for intake, including filename, raw text content, ingestion timestamp, and processing status.
- **Order Draft**: The working reconciliation entity representing an intake session. Contains order header attributes (customer identity, purchase order reference number, optional customer-stated order total), reference to the customer contract, draft status (`Ingested`, `Needs Review`, `Ready for Approval`, `Approved`, `Rejected`), and list of line drafts.
- **Draft Line Item**: An extracted order line containing original customer description, extracted quantity, customer unit price, grounded text snippet reference, matched catalog SKU, match confidence level, candidate SKU suggestions, validated contract/tier price, calculated line total, and discrepancy flags.
- **Catalog Product (SKU)**: An item from the internal product master, including SKU code, standard product name, product category, unit of measure, base unit price, and minimum order quantity.
- **Customer Contract**: Commercial terms established with a specific customer, including customer identifier, contracted pricing rules/tiers per SKU, and contract validity window.
- **Discrepancy Flag**: A structured indicator associated with an order draft or line item detailing discrepancy type (strictly `PriceMismatch`, `QuantityOrPackagingBreach`, `ArithmeticMismatch`, or `CatalogMatchingMismatch`), severity level, expected value, requested value, and resolution state (resolved by field correction to match source document, resolved by line removal, or unresolved). Unresolved discrepancies strictly block `Ready for Approval`.
- **Verified Order Record**: The committed, finalized commercial order created upon explicit operator approval, containing immutable line items, approved prices and quantities, grand total, approving operator identity, and approval timestamp.
- **Audit Event**: An immutable log record capturing every lifecycle transition, field modification, operator adjustment (field corrections to match source document, SKU selections, line removals), draft rejection, and approval action.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001 (Prepared Demo Processing Velocity)**: An operations coordinator can complete the end-to-end reconciliation and approval of a prepared 5-line purchase order in under 60 seconds during the demo scenario.
- **SC-002 (Discrepancy Catch Rate)**: 100% of seeded pricing mismatches, arithmetic errors, and minimum order quantity violations in prepared test orders are detected and flagged prior to operator approval.
- **SC-003 (Zero Hallucinated Commitments)**: 100% of ambiguous or out-of-catalog customer descriptions are routed to operator review; zero unrecognized descriptions are automatically assigned to an unverified SKU.
- **SC-004 (Full Provenance Visibility)**: 100% of extracted line items provide visible textual grounding citations linking back to the source purchase order text.
- **SC-005 (Mandatory Gate Enforcement)**: 0% of unreviewed or discrepancy-laden order drafts can transition to a committed order record without explicit operator sign-off or resolution.
- **SC-006 (Explicit Failure & Replay Transparency)**: In the event of an unreadable document or immediately detectable AI provider failure (local parse errors, connection refusal, authentication failure, quota/rate-limit rejection, or explicit upstream 4xx/5xx), the system presents an explicit diagnostic error with a target of ≤5 seconds from request intake without partial data corruption or silent fallback. Healthy live inference operates within a bounded ≤15-second budget; silent/stalled provider inference is aborted at ≤15 seconds with an explicit diagnostic error and no partial persistence. No automatic provider failover or fixture substitution is permitted. When replay or fixture mode is active, 100% of views visibly indicate non-live demonstration status.

## Assumptions

- **Target Users & Context**: Primary users are wholesale operations and order-entry coordinators working via desktop web browsers who possess domain familiarity with catalog products and customer relationships.
- **Input Scope for MVP**: Inbound purchase orders are provided as digital UTF-8 text (`.txt`) or digital PDFs with extractable text streams (`.pdf`). Scanned physical image OCR, handwritten documents, spreadsheets, images, and multi-currency conversions are explicitly out of scope for the MVP.
- **Catalog & Contract Fixtures**: A representative catalog dataset (10–30 wholesale products) and customer contract terms (with tiered pricing and MOQs) are pre-loaded to support deterministic validation and test fixtures.
- **Division of Responsibilities**: AI models are strictly constrained to fuzzy interpretation tasks (document field extraction, semantic SKU ranking, and ambiguity detection). All calculations, pricing lookups, rule enforcement, status transitions, and data storage are strictly deterministic.
- **Fallback Operations & Demo Modes**: To guarantee demo reliability in the event of external network or API provider latency/outages, the system supports reproducible fixture/replay modes using synthetic documents. Any replay or fixture execution is explicitly initiated and visibly badged as non-live, and never acts as a silent fallback for failed live user submissions.
