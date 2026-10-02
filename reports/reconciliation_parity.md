# Reconciliation Parity Report: Legacy Engine vs Symbolic Rules

- **Generated At**: `2026-10-02T10:47:10.926966+00:00`
- **Seed**: `20261002`
- **Total Cases**: `203`
- **Matched Cases**: `203`
- **Mismatched Cases**: `0`
- **Overall Agreement**: `100.00%`

## 1. Executive Summary

> [!NOTE]
> **100% Deterministic Parity Confirmed** across all evaluated order fixtures and stratified pseudo-random order drafts for the four core discrepancy categories: `PriceMismatch`, `QuantityOrPackagingBreach`, `ArithmeticMismatch.line`, and `ArithmeticMismatch.order`.

## 2. Fixture Manifest & Evaluation

### Evaluated Suitable Fixtures

| Fixture ID | Source Document | Status | Parity Result |
|---|---|---|---|
| `fixture-clean-acme` | `File: tests/fixtures/po_clean_acme.txt` | Evaluated | **MATCH (100%)** |
| `fixture-discrepancy-apex` | `File: tests/fixtures/po_discrepancy_apex.txt` | Evaluated | **MATCH (100%)** |
| `fixture-ambiguous-apex` | `File: tests/fixtures/po_ambiguous_apex.txt` | Evaluated | **MATCH (100%)** |

### Excluded Fixtures Manifest

| Fixture File | Rationale for Exclusion |
|---|---|
| `po_unextractable.pdf` | Document parsing raises UnextractableTextError because the PDF contains no extractable text layers. Ingestion fails before an OrderDraft can be produced. |

## 3. Stratification Breakdown (Pseudo-Random Orders)

Generated exactly 200 pseudo-random orders stratified across 9 distinct categories using deterministic seed `20261002`:

| Stratum | Order Count | Description |
|---|---|---|
| `clean` | 25 | Clean orders with contract pricing match, exact arithmetic, and no breaches |
| `price_mismatch` | 25 | Customer-stated unit price deviates from contract tier (including 1-cent boundaries) |
| `moq_shortfall` | 25 | Quantity ordered is below catalog minimum order quantity (MOQ) |
| `pkg_breach` | 25 | Quantity satisfies MOQ but breaches package increment multiplicity |
| `arith_line` | 25 | Stated line total != quantity * unit price (including 1-cent boundaries) |
| `arith_order` | 25 | Stated order total != sum of stated line totals (including 1-cent boundaries) |
| `mixed` | 25 | Multi-line combinations featuring multiple concurrent discrepancy types |
| `unresolved_ambiguous` | 15 | Incomplete inputs, missing totals, ambiguous SKUs, and unrecognized customers |
| `removed_lines` | 10 | Multi-line drafts containing Active and Removed line items |

## 4. Parity Metrics by Category

| Discrepancy Category | Total Cases | True Positives | True Negatives | False Positives | False Negatives | Matches | Agreement (%) |
|---|---|---|---|---|---|---|---|
| `PriceMismatch` | 203 | 56 | 147 | 0 | 0 | 203 | **100.00%** |
| `QuantityOrPackagingBreach` | 203 | 77 | 126 | 0 | 0 | 203 | **100.00%** |
| `ArithmeticMismatch.line` | 203 | 34 | 169 | 0 | 0 | 203 | **100.00%** |
| `ArithmeticMismatch.order` | 203 | 30 | 173 | 0 | 0 | 203 | **100.00%** |

## 5. Non-Covered / Unmodeled Discrepancies

The legacy engine produces additional flags that are outside the scope of `RECONCILIATION_RULES`. These are monitored and verified not to cause false parity discrepancies:

| Discrepancy Type | Legacy Invocations | Handling |
|---|---|---|
| `CatalogMatchingMismatch` | 7 | Non-covered by symbolic rules; tracked as out-of-scope |

## 6. Mismatch Analysis

No mismatches detected. Both engines produced identical binary decisions across all categories.

