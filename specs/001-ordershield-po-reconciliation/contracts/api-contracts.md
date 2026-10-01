# API Contracts: OrderShield Purchase Order Reconciliation

**Feature Branch**: `001-ordershield-po-reconciliation`  
**Date**: 2026-09-29  
**Status**: Ready for Planning (Human Gate Final Reconciliation)  

---

## 1. Endpoints Overview

All endpoints communicate via JSON over HTTP and follow RESTful conventions under `/api/v1`. Live intake and fixture replay intake are **strictly separated at the API boundary**.

| Method | Endpoint | Description | Mode |
|:---|:---|:---|:---:|
| `POST` | `/api/v1/orders/ingest` | Upload user document (`.txt` or `.pdf`) and run live inference | **Live Only** |
| `GET` | `/api/v1/fixtures` | List pre-registered synthetic demo fixtures | Neutral |
| `POST` | `/api/v1/fixtures/{fixture_id}/ingest` | Ingest pre-registered fixture using `FixtureAIProvider` | **Replay Only** (`is_replay_mode=true`) |
| `GET` | `/api/v1/catalog` | Search and list products in the catalog master | Neutral |
| `GET` | `/api/v1/drafts/{draft_id}` | Fetch full draft details, active lines, discrepancies, and status | Neutral |
| `PATCH` | `/api/v1/drafts/{draft_id}/lines/{line_id}` | Grounded field correction or catalog SKU selection | Neutral (Non-terminal) |
| `DELETE` | `/api/v1/drafts/{draft_id}/lines/{line_id}` | Remove non-compliant or invalid line item | Neutral (Non-terminal) |
| `POST` | `/api/v1/drafts/{draft_id}/approve` | Approve draft and create immutable `VerifiedOrderRecord` | Neutral (Requires `Ready for Approval`) |
| `POST` | `/api/v1/drafts/{draft_id}/reject` | Reject draft with mandatory recorded reason | Neutral (Non-terminal) |
| `GET` | `/api/v1/orders/{order_id}` | Retrieve committed order record with audit events and provenance | Neutral |

---

## 2. Request & Response Schemas

### 2.1 Catalog Lookup
`GET /api/v1/catalog`

Supports searching the master product catalog when an operator resolves an unrecognized or ambiguous SKU.

**Query Parameters**:
- `query` (optional string): Keyword match against SKU code or product name.
- `category` (optional string): Filter by category.

**Response**: `200 OK`
```json
[
  {
    "sku": "SKU-WRAP-18",
    "name": "Industrial Stretch Film 18in 80ga",
    "category": "Packaging",
    "unit_of_measure": "Roll",
    "base_price": "24.50",
    "min_order_quantity": 5,
    "package_increment": 1
  },
  {
    "sku": "SKU-WRAP-15",
    "name": "Standard Pallet Wrap 15in 65ga",
    "category": "Packaging",
    "unit_of_measure": "Case",
    "base_price": "20.00",
    "min_order_quantity": 5,
    "package_increment": 1
  }
]
```

---

### 2.2 Live Document Intake
`POST /api/v1/orders/ingest`

Uploads an incoming customer purchase order document. **Always executes via `LiveAIProvider`** using the explicitly configured provider (`qwen3.8-flash` primary or `gemini-3.5-flash-lite` secondary). Zero automatic runtime failover or silent provider substitution is performed. It does not accept any parameter to substitute pre-recorded fixtures.

**Request**: `multipart/form-data`
- `file`: File binary (`.txt` or `.pdf`)

**Response**: `201 Created`
```json
{
  "draft_id": "8f8b8240-429a-41d1-8cf9-f9c3eb06f1aa",
  "document_id": "11928374-429a-41d1-8cf9-f9c3eb06f1bb",
  "customer_id": "CUST-ACME",
  "customer_name_extracted": "Acme Industrial Supplies",
  "po_number_extracted": "PO-99214",
  "status": "Needs Review",
  "is_replay_mode": false,
  "calculated_subtotal": "450.00",
  "header_provenance": {
    "customer_name": {
      "field_name": "customer_name",
      "verbatim_snippet": "Acme Industrial Supplies",
      "location": { "type": "txt", "line_number": 2, "char_offset": 0 }
    },
    "po_number": {
      "field_name": "po_number",
      "verbatim_snippet": "PO-99214",
      "location": { "type": "txt", "line_number": 4, "char_offset": 0 }
    }
  },
  "line_items": [
    {
      "line_id": "line-001",
      "line_number": 1,
      "customer_description": "18in stretch film heavy duty",
      "extracted_quantity": 10,
      "extracted_unit_price": "25.00",
      "extracted_line_total": "250.00",
      "matched_sku": "SKU-WRAP-18",
      "sku_name": "Industrial Stretch Film 18in 80ga",
      "sku_confidence": "High",
      "sku_resolution_source": "AI_HIGH_CONFIDENCE",
      "candidate_skus": [],
      "contract_price": "25.00",
      "calculated_line_total": "250.00",
      "status": "Active",
      "field_provenance": {
        "customer_description": {
          "field_name": "customer_description",
          "verbatim_snippet": "18in stretch film heavy duty",
          "location": { "type": "txt", "line_number": 12, "char_offset": 10 }
        },
        "extracted_quantity": {
          "field_name": "extracted_quantity",
          "verbatim_snippet": "10 rolls",
          "location": { "type": "txt", "line_number": 12, "char_offset": 0 }
        },
        "extracted_unit_price": {
          "field_name": "extracted_unit_price",
          "verbatim_snippet": "$25.00",
          "location": { "type": "txt", "line_number": 12, "char_offset": 42 }
        },
        "extracted_line_total": {
          "field_name": "extracted_line_total",
          "verbatim_snippet": "$250.00",
          "location": { "type": "txt", "line_number": 12, "char_offset": 55 }
        }
      },
      "discrepancies": []
    },
    {
      "line_id": "line-002",
      "line_number": 2,
      "customer_description": "Standard pallet wrap",
      "extracted_quantity": 2,
      "extracted_unit_price": "20.00",
      "extracted_line_total": "40.00",
      "matched_sku": null,
      "sku_name": null,
      "sku_confidence": "Ambiguous",
      "sku_resolution_source": "NONE",
      "candidate_skus": [
        {
          "sku": "SKU-WRAP-15",
          "name": "Standard Pallet Wrap 15in 65ga",
          "contract_price": "20.00",
          "score": 0.88,
          "rationale": "High semantic match for standard gauge pallet film"
        },
        {
          "sku": "SKU-WRAP-18",
          "name": "Industrial Stretch Film 18in 80ga",
          "contract_price": "25.00",
          "score": 0.74,
          "rationale": "Alternative width match"
        }
      ],
      "contract_price": null,
      "calculated_line_total": "0.00",
      "status": "Active",
      "field_provenance": {
        "customer_description": {
          "field_name": "customer_description",
          "verbatim_snippet": "Standard pallet wrap",
          "location": { "type": "txt", "line_number": 13, "char_offset": 6 }
        },
        "extracted_quantity": {
          "field_name": "extracted_quantity",
          "verbatim_snippet": "2 cs",
          "location": { "type": "txt", "line_number": 13, "char_offset": 0 }
        },
        "extracted_unit_price": {
          "field_name": "extracted_unit_price",
          "verbatim_snippet": "$20.00",
          "location": { "type": "txt", "line_number": 13, "char_offset": 32 }
        },
        "extracted_line_total": {
          "field_name": "extracted_line_total",
          "verbatim_snippet": "$40.00",
          "location": { "type": "txt", "line_number": 13, "char_offset": 45 }
        }
      },
      "discrepancies": [
        {
          "flag_id": "flag-001",
          "discrepancy_type": "CatalogMatchingMismatch",
          "severity": "Blocking",
          "expected_value": "Explicit SKU Selection",
          "requested_value": "Standard pallet wrap",
          "explanation": "Customer description matched multiple catalog items; operator must confirm intended SKU.",
          "resolution_state": "Unresolved"
        },
        {
          "flag_id": "flag-002",
          "discrepancy_type": "QuantityOrPackagingBreach",
          "severity": "Blocking",
          "expected_value": "MOQ: 5",
          "requested_value": "Qty: 2",
          "explanation": "Requested quantity of 2 is below minimum order quantity of 5.",
          "resolution_state": "Unresolved"
        }
      ]
    }
  ]
}
```

**Explicit Error Responses (Target ≤5s from request intake for immediately detectable errors / bounded ≤15s live inference timeout)**:
- `400 Bad Request`: If document is unreadable, empty, or a PDF lacking extractable text (`UnextractableTextError`, target ≤5s from request intake).
- `502 Bad Gateway`: If live AI provider returns invalid JSON or schema validation fails (`AIOutputValidationError`).
- `503 Service Unavailable`: If live AI provider fails, encounters immediately detectable transport/auth/quota errors (target ≤5s from request intake), or reaches the bounded 15.0 s inference timeout (`AIProviderUnavailableError`). Silent/stalled provider inference is aborted at ≤15s with an explicit diagnostic error per approved SC-006 amendment. No automatic runtime provider failover or silent substitution is performed.

---

### 2.2.1 AI Extraction & Persistence Contract Boundary (`AIExtractionPayload`)

AI providers output an untrusted extraction payload adhering to `AIExtractionPayload`. It may carry an optional source-stated order total:

```json
{
  "customer_name": "Acme Industrial Supplies",
  "customer_id": "CUST-ACME",
  "po_number": "PO-99214",
  "extracted_order_total": "350.00",
  "header_provenance": {
    "customer_name": {
      "field_name": "customer_name",
      "verbatim_snippet": "Acme Industrial Supplies",
      "location": { "type": "txt", "line_number": 2, "char_offset": 0 }
    },
    "po_number": {
      "field_name": "po_number",
      "verbatim_snippet": "PO-99214",
      "location": { "type": "txt", "line_number": 4, "char_offset": 0 }
    },
    "extracted_order_total": {
      "field_name": "extracted_order_total",
      "verbatim_snippet": "$350.00",
      "location": { "type": "txt", "line_number": 15, "char_offset": 0 }
    }
  },
  "line_items": []
}
```

**Persistence Boundary Semantics**:
- `extracted_order_total` and its provenance are optional.
- If present, its provenance is strictly mandatory and grounded against canonical `PurchaseOrderDocument.raw_text`.
- Upon successful grounding, `order_service` converts `extracted_order_total` to exact integer cents and persists it into `OrderDraft.extracted_order_total_cents` (`35000`) and creates a header-level `FieldProvenance` row (`field_name="extracted_order_total"`, `line_item_id=null`).
- If omitted from the customer PO, `OrderDraft.extracted_order_total_cents` is persisted as `null` and no order-total provenance record is created.
- The public `OrderDraftResponse` exposed by the intake API intentionally serves reconciliation workflow state, presenting the deterministic `calculated_subtotal` for pricing verification while the untrusted raw `extracted_order_total_cents` remains anchored at the internal persistence boundary for discrepancy evaluation.

---

### 2.3 Replay / Fixture Intake (Dedicated Endpoint)
`GET /api/v1/fixtures`  
Lists pre-registered demonstration fixtures.

`POST /api/v1/fixtures/{fixture_id}/ingest`  
Ingests a pre-registered fixture document via `FixtureAIProvider`. Always returns `OrderDraftResponse` with **`"is_replay_mode": true`**.

---

### 2.4 Update Line Item (Grounded Correction & Catalog SKU Selection)
`PATCH /api/v1/drafts/{draft_id}/lines/{line_id}`

#### Action 1: `SelectSKU`
Allows selecting any valid SKU from the master catalog (not limited to AI candidates). Sets `sku_resolution_source = "OPERATOR_SELECTED"`, evaluates contract price tiers, and recomputes discrepancies.

**Request**: `application/json`
```json
{
  "action": "SelectSKU",
  "matched_sku": "SKU-WRAP-15"
}
```

#### Action 2: `CorrectField` (Server-Side Grounding Validation)
Allows correcting a mis-extracted field ONLY when the correction is grounded in the source document.
**Request**: `application/json`
```json
{
  "action": "CorrectField",
  "field": "extracted_quantity",
  "value": 10,
  "source_snippet": "10 cs",
  "source_location": {
    "type": "txt",
    "line_number": 13,
    "char_offset": 0
  }
}
```

**Validation & Error Responses**:
- `422 Unprocessable Entity`: If `source_snippet` is not found at `source_location` in the canonical `PurchaseOrderDocument.raw_text`, or if `value` does not match the snippet:
  ```json
  {
    "error": "SourceGroundingMismatchError",
    "message": "Field correction value '10' is not grounded in the source document at the specified location."
  }
  ```
- `409 Conflict`: If draft is in terminal state `Approved` or `Rejected` (`TerminalDraftConflictError`).

---

### 2.5 Remove Line Item (Preserving Discrepancy History)
`DELETE /api/v1/drafts/{draft_id}/lines/{line_id}`

Marks the line item as `status = "Removed"` and updates all associated unresolved `DiscrepancyFlag` records to `resolution_state = "ResolvedByLineRemoval"`. **Discrepancy records are not deleted.**

**Response**: `200 OK` (Updated `OrderDraftResponse` with recalculated subtotal).  
**Terminal Conflict Error**: `409 Conflict` if draft is in terminal state.

---

### 2.6 Approve Order Draft
`POST /api/v1/drafts/{draft_id}/approve`

Executes in a single atomic database transaction.

**Request**: `application/json`
```json
{
  "operator_id": "op-sarah"
}
```

**Response**: `200 OK`
```json
{
  "order_id": "vo-8829-acme",
  "draft_id": "8f8b8240-429a-41d1-8cf9-f9c3eb06f1aa",
  "order_number": "VO-2026-0001",
  "customer_id": "CUST-ACME",
  "po_number": "PO-99214",
  "grand_total": "350.00",
  "approved_by": "op-sarah",
  "approved_at": "2026-09-29T21:40:15Z",
  "is_replay_mode": false,
  "line_items_count": 2
}
```

**Conflict Errors**:
- `409 Conflict` (`DraftNotReadyForApprovalError`): If draft is in `Needs Review` due to unmapped SKUs or unresolved discrepancies.
- `409 Conflict` (`TerminalDraftConflictError`): If draft is already in terminal state `Approved` or `Rejected`. Repeated approvals will never create duplicate verified orders.

---

### 2.7 Reject Order Draft
`POST /api/v1/drafts/{draft_id}/reject`

**Request**: `application/json`
```json
{
  "operator_id": "op-sarah",
  "reason": "Customer pricing is non-compliant and customer refused order revision."
}
```

**Response**: `200 OK` (`OrderDraftResponse` with `status: "Rejected"`).  
**Terminal Conflict Error**: `409 Conflict` if draft is already in terminal state.

---

### 2.8 Inspect Verified Order & Audit Trail
`GET /api/v1/orders/{order_id}`

**Response**: `200 OK`
```json
{
  "order_id": "vo-8829-acme",
  "order_number": "VO-2026-0001",
  "customer_id": "CUST-ACME",
  "po_number": "PO-99214",
  "grand_total": "350.00",
  "approved_by": "op-sarah",
  "approved_at": "2026-09-29T21:40:15Z",
  "is_replay_mode": false,
  "header_provenance": {
    "customer_name": {
      "verbatim_snippet": "Acme Industrial Supplies",
      "location": { "type": "txt", "line_number": 2, "char_offset": 0 }
    },
    "po_number": {
      "verbatim_snippet": "PO-99214",
      "location": { "type": "txt", "line_number": 4, "char_offset": 0 }
    }
  },
  "line_items": [
    {
      "line_number": 1,
      "sku": "SKU-WRAP-18",
      "sku_name": "Industrial Stretch Film 18in 80ga",
      "sku_resolution_source": "AI_HIGH_CONFIDENCE",
      "quantity": 10,
      "unit_price": "25.00",
      "line_total": "250.00",
      "field_provenance": {
        "customer_description": { "verbatim_snippet": "18in stretch film heavy duty", "location": { "type": "txt", "line_number": 12, "char_offset": 10 } },
        "extracted_quantity": { "verbatim_snippet": "10 rolls", "location": { "type": "txt", "line_number": 12, "char_offset": 0 } },
        "extracted_unit_price": { "verbatim_snippet": "$25.00", "location": { "type": "txt", "line_number": 12, "char_offset": 42 } },
        "extracted_line_total": { "verbatim_snippet": "$250.00", "location": { "type": "txt", "line_number": 12, "char_offset": 55 } }
      }
    }
  ],
  "audit_trail": [
    {
      "event_type": "DocumentIngested",
      "actor": "System",
      "timestamp": "2026-09-29T21:38:00Z",
      "details": { "filename": "PO_99214.pdf" }
    },
    {
      "event_type": "AIExtractionCompleted",
      "actor": "AIProvider",
      "timestamp": "2026-09-29T21:38:03Z",
      "details": { "lines_extracted": 2 }
    },
    {
      "event_type": "SKUSelected",
      "actor": "Operator (op-sarah)",
      "timestamp": "2026-09-29T21:39:10Z",
      "details": { "line_number": 2, "selected_sku": "SKU-WRAP-15", "resolution_source": "OPERATOR_SELECTED" }
    },
    {
      "event_type": "OrderApproved",
      "actor": "Operator (op-sarah)",
      "timestamp": "2026-09-29T21:40:15Z",
      "details": { "grand_total": "350.00" }
    }
  ]
}
```

---

## 3. UI Presentation Contract & Replay Invariants

1. **Zero External CDN Dependencies**: All CSS stylesheets, layout grids, icons, and JavaScript components MUST be served directly from local committed assets (`/static/css/`, `/static/js/`). The application MUST render and operate completely offline.
2. **Replay Mode Banner**: Whenever `is_replay_mode == true`, the frontend MUST render an unclosable top banner:
   ```html
   <div class="banner-nonlive bg-amber-500 text-black font-bold p-2 text-center">
     ⚠️ DEMO / REPLAY MODE (NON-LIVE FIXTURE DATA)
   </div>
   ```
3. **Manual Catalog Search Modal**: When an operator resolves an unrecognized or ambiguous line item, the UI allows searching the catalog master via `GET /api/v1/catalog?query=...` and selecting any valid catalog SKU.
4. **Field Grounding Inspection**: Clicking any extracted field opens a provenance drawer displaying the exact verbatim snippet from the document, its canonical location, and matching confidence.
