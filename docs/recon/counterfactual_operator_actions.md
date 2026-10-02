# Counterfactual Analysis of Allowed Operator Actions

## 1. Overview & Objective

The `app.symbolic.counterfactuals` module implements a deterministic counterfactual dry-run evaluator and bounded action search for purchase order drafts. It explores discrete sequences of permitted operator actions in isolated in-memory sandboxes to answer:

> **Which permitted operator actions or sequences transition an order draft into `Ready for Approval` without modifying the source state and without applying unauthorized commercial overrides?**

This is **not** a numerical optimizer or price/quantity tuner. Customer-stated commercial terms (`extracted_quantity`, `extracted_unit_price_cents`, `extracted_line_total_cents`, `extracted_order_total_cents`) are strictly immutable.

---

## 2. Strict Action Space & Allowlist

Only three typed operator actions are permitted:

| Action DTO | Semantics | Invariants & Constraints |
|---|---|---|
| `SelectSKU(line_id, sku)` | Explicitly resolves line catalog ambiguity to a valid master-catalog SKU. | Validates draft mutability, line ownership, and catalog product existence. Uses real candidate SKUs or explicit operator inputs. Does not scan catalog looking for convenient pricing. |
| `RemoveLine(line_id)` | Marks an active line item as `Removed`. | Preserves row, provenance, and discrepancy history. Reconciles line flags and order-level arithmetic. Does not physically delete rows. |
| `RequestCorrectedPO([reason])` | Proposes requesting a revised purchase order from customer. | **Always** yields `outcome="requires_external_input"`. Never fabricates prices/quantities, never clears blockers, and never declares `Ready for Approval`. |

### Strictly Prohibited Actions & Parameters
The evaluator fails closed and rejects:
- `CorrectField` or any action outside the allowlist;
- Any payload containing commercial override fields (`extracted_quantity`, `extracted_unit_price`, `extracted_line_total`, `extracted_order_total`, `contract_price`, `price`, `quantity`, etc.);
- Modifications of contract tiers, catalog MOQ, or package increments;
- Manual alterations of `discrepancy_flags` resolution state or `draft.status`.

*(Note: Recalculation of derived values `contract_price_cents`, `calculated_line_total_cents`, and `calculated_subtotal_cents` by the existing reconciliation engine upon valid mutations is expected and permitted.)*

---

## 3. Authoritative Success Criterion

Readiness is evaluated strictly by the production `evaluate_clean_draft` service function against the simulated draft in the sandbox:

$$\text{Success} \iff \text{simulated\_draft.status} == \text{"Ready for Approval"}$$

The evaluator **does not** consider the following sufficient:
- Absence of only the 3 modeled symbolic rule categories;
- Absence of flags without structural data completeness;
- High SKU match confidence or low review priority.

The evaluator **never** calls `approve_order`. The analysis stops at `Ready for Approval`.

---

## 4. Sandbox Isolation & Zero Source State Side Effects

To guarantee total isolation:
1. **Zero Caller Session Touches**: The source database session is read strictly under `with source_db.no_autoflush:`. The evaluator **never** calls `flush()`, `commit()`, or `rollback()` on the caller session.
2. **Pending Writes Preserved**: Uncommitted objects or dirty attributes in the caller session remain untouched in `source_db.new` / `source_db.dirty`.
3. **Dedicated In-Memory SQLite Sandbox**: Each plan evaluation constructs a brand-new `sqlite:///:memory:` database engine and session.
4. **Independent Model Graph**: Catalog products, customer contracts, contract tiers, documents, drafts, lines, provenances, and discrepancy flags are cloned by attribute value. No ORM relationship objects or mutable collections are shared.
5. **Contextual Contracts**: The sandbox mirrors the caller database's active customer contracts and tiers rather than relying on hardcoded baseline seeds.
6. **Pure Python DTOs**: Sandboxes are disposed immediately in a `finally:` block. No detached or expired ORM objects leak across the API boundary.

---

## 5. Bounded Deterministic Search

The `search_counterfactual_plans` function systematically explores discrete action sequences:

- **Already Ready Check**: If `draft.status == "Ready for Approval"` initially, returns 0 actions required.
- **Depth 1**: Evaluates single-line actions (`RemoveLine`, `SelectSKU` with candidate SKUs) plus `RequestCorrectedPO`.
- **Depth 2**: Evaluates canonical pairs of actions across distinct lines (`line1 < line2`), pruning redundant commutations and same-line duplicates.
- **Search Limits**: Default `max_depth = 2` and `max_scenarios = 50`. When the scenario quota is reached, search halts deterministically and sets `is_truncated = True`.
- **Ranking**: Successful plans are ordered shortest-first.

---

## 6. Discrepancy Apex (`fixture-discrepancy-apex`) Dry-Run Results

Evaluation of the authoritative `po_discrepancy_apex.txt` fixture under seed baseline contract terms:
- **Line 1**: Qty 10, Stated \$18.00 vs Contract Tier \$19.00 (`PriceMismatch`).
- **Line 2**: Qty 2, Stated \$20.00, Ambiguous SKU (`CatalogMatchingMismatch`). Candidate SKUs: `SKU-WRAP-15` (MOQ 5) and `SKU-WRAP-18` (MOQ 5).

| Plan | Actions | Outcome | Simulated Status | Remaining Blockers | Explanation |
|:---:|---|:---:|:---:|---|---|
| **0** | *(Initial State)* | `blocked` | `Needs Review` | Line 1: `PriceMismatch`<br>Line 2: `CatalogMatchingMismatch` | Initial draft has unverified price and ambiguous SKU. |
| **1** | `SelectSKU(Line 2, "SKU-WRAP-15")` | `blocked` | `Needs Review` | Line 1: `PriceMismatch`<br>Line 2: `QuantityOrPackagingBreach` (Qty 2 < MOQ 5) | Resolves SKU ambiguity but reveals MOQ breach; Line 1 price mismatch persists. |
| **2** | `SelectSKU(Line 2, "SKU-WRAP-18")` | `blocked` | `Needs Review` | Line 1: `PriceMismatch`<br>Line 2: `QuantityOrPackagingBreach` (Qty 2 < MOQ 5) | Resolves SKU ambiguity but reveals MOQ breach; Line 1 price mismatch persists. |
| **3** | `RemoveLine(Line 1)` | `blocked` | `Needs Review` | Line 2: `CatalogMatchingMismatch` | Eliminates Line 1 price mismatch, but Line 2 SKU remains ambiguous. |
| **4** | `RemoveLine(Line 2)` | `blocked` | `Needs Review` | Line 1: `PriceMismatch` | Eliminates Line 2 ambiguity, but Line 1 price mismatch persists. |
| **5** | `RemoveLine(Line 1)` +<br>`SelectSKU(Line 2, "SKU-WRAP-15")` | `blocked` | `Needs Review` | Line 2: `QuantityOrPackagingBreach` (Qty 2 < MOQ 5) | Commercial override forbidden (cannot change qty 2 to 5); MOQ breach persists. |
| **6** | `RemoveLine(Line 1)` +<br>`RemoveLine(Line 2)` | `blocked` | `Needs Review` | *Data Incompleteness*: No active line items remain in draft | Empty order cannot be approved. |
| **7** | `RequestCorrectedPO()` | `requires_external_input` | `None` | Line 1: `PriceMismatch`<br>Line 2: `CatalogMatchingMismatch` | Customer must provide a corrected PO; blockers cannot be fabricated away. |

**Search Result on `discrepancy_apex`**:
$$\text{successful\_plans} = () \quad (\text{empty tuple})$$
Because commercial overrides (changing Line 1 price to \$19.00 or Line 2 quantity to 5) are strictly prohibited, **no permitted operator action can legitimately make `discrepancy_apex` Ready for Approval**.

---

## 7. Positive Controls (Valid Paths to Ready)

To verify that the engine is not permanently blocked, positive controls validate genuine paths to readiness:

1. **`fixture-ambiguous-apex` (Single Ambiguous Line)**:
   - Line 1 has Qty 10, Stated \$20.00, Ambiguous SKU (`SKU-WRAP-15` or `SKU-WRAP-18`).
   - For `SKU-WRAP-15`, Contract Tier price is \$20.00 and MOQ is 5 (Qty 10 compliant).
   - Plan: `[SelectSKU(Line 1, "SKU-WRAP-15")]`
   - **Outcome**: `ready` $\to$ `Ready for Approval` (Successful plan found at depth 1).

2. **Order with One Clean Line and One Flawed Line**:
   - Line 1 is clean (`SKU-WRAP-15`, Qty 10, \$20.00).
   - Line 2 has an unresolvable price discrepancy (`PriceMismatch`).
   - Plan: `[RemoveLine(Line 2)]`
   - **Outcome**: `ready` $\to$ `Ready for Approval` (Line 1 remains active and compliant).

---

## 8. Python API Usage

```python
from app.symbolic import (
    SelectSKUAction,
    RemoveLineAction,
    RequestCorrectedPOAction,
    evaluate_action_plan,
    search_counterfactual_plans,
)

# 1. Evaluate an explicit action plan
result = evaluate_action_plan(
    db_session,
    draft_id="draft-uuid",
    actions=[
        SelectSKUAction(line_id="line-uuid", sku="SKU-WRAP-15"),
    ],
)
print(result.outcome)           # "ready" | "blocked" | "requires_external_input" | "invalid"
print(result.simulated_status)  # "Ready for Approval" | "Needs Review" | None
print(result.is_successful)     # True | False

# 2. Search for successful action sequences
analysis = search_counterfactual_plans(
    db_session,
    draft_id="draft-uuid",
    max_depth=2,
    max_scenarios=50,
)
for plan in analysis.successful_plans:
    print("Found plan:", plan.actions)
```

---

## 9. Limitations & Operational Boundaries

1. **RequestCorrectedPO is an External Gate**: Proposing a corrected PO does not synthesize document content, clear blockers, or transition draft status. Ingestion of a revised customer PO remains a separate downstream workflow.
2. **Read-Only Advisory Analysis**: The counterfactual engine only performs sandbox simulation. It never persists recommendations, does not automatically execute mutations, and does not alter production drafts.
