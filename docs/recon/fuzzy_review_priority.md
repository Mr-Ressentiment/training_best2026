# Fuzzy Purchase Order Manual Review Priority Specification

## 1. Overview

The `app.symbolic.review_priority` module provides a deterministic, explainable evaluation of purchase order review urgency. It computes:
- `review_priority`: calibrated continuous priority score in $[0.0, 1.0]$.
- `review_priority_label`: linguistic categorization in `{'low', 'medium', 'high'}`.

### Core Invariant
**Strict Discrepancy Count Monotonicity**:
All else being equal, increasing `discrepancy_count` **cannot** decrease either numeric `review_priority` or its linguistic label:
$$\text{review\_priority}(n + 1, x) \ge \text{review\_priority}(n, x) \quad \text{and} \quad \text{rank}(\text{label}(n + 1, x)) \ge \text{rank}(\text{label}(n, x))$$

This invariant is guaranteed **by design** using the **monotonic upper envelope**:
$$\text{final}(n, x) = \max_{k \in \{0, \dots, \min(n, \text{COUNT\_CAP})\}} \text{base}(k, x)$$
where $\text{COUNT\_CAP} = 5$.

---

## 2. Input Features & Universe Normalization

The module accepts raw or aggregated order metrics and maps them deterministically to normalized variables:

| Input Parameter | Raw Universe | Normalized Universe | Normalization & Aggregation Rule |
|---|---|---|---|
| `discrepancy_count` | Integer $\ge 0$ | $[0.0, 5.0]$ | Saturated at $\text{COUNT\_CAP} = 5$. Rejects negative integers, floats, and booleans. |
| `discrepancy_severity` | $[0.0, 1.0]$ or string | $[0.0, 1.0]$ | Strings parsed: `'Info' \to 0.20`, `'Warning' \to 0.50`, `'Blocking' \to 0.85`. Multiple items aggregated via $\max$. |
| `price_deviation_pct` | Relative \% | $[0.0, 1.0]$ | Symmetrical $|deviation|$. Aggregated via $\max(|dev|)$. Saturated at $50.0\% \to 1.0$. |
| `order_total_cents` | Integer cents $\ge 0$ | $[0.0, 1.0]$ | Saturated at $\text{ORDER\_TOTAL\_SATURATION\_CENTS} = 1\_000\_000$ cents (\$10,000.00). Rejects booleans and negative values. |
| `match_confidence` | $[0.0, 1.0]$ | $[0.0, 1.0]$ | From SKU confidence module. Inverted to `uncertainty` = $1.0 - \text{match\_confidence}$. |

### Effective Discrepancy Severity
Discrepancy severity and relative price deviation are coupled via monotonic maximum:
$$\text{effective\_severity} = \max\left(\text{base\_severity}, \min\left(1.0, \frac{\max(|price\_deviation\_pct|)}{50.0}\right)\right)$$

Adding another discrepancy (even minor) to an existing list can only increase or preserve count and effective severity, ensuring non-decreasing priority.

### Conservative Incomplete Data Policy
- When `order_total_cents` is omitted, the engine assumes moderate financial exposure ($0.50$) and activates `has_incomplete_data = True`.
- When `match_confidence` is omitted, the engine assumes maximum uncertainty ($1.0$) and activates `has_incomplete_data = True`.
- Incomplete input activates `INCOMPLETE_DATA_FLOOR` ($0.45$), guaranteeing at least `'medium'` priority.

---

## 3. Membership Functions

### 3.1. Linguistic Variables
- **`count`** ($[0.0, 5.0]$):
  - `zero`: $\text{trap}(0.0, 0.0, 0.2, 0.8)$
  - `few`: $\text{tri}(0.5, 1.8, 3.2)$
  - `many`: $\text{trap}(2.5, 3.8, 5.0, 5.0)$
- **`severity`** ($[0.0, 1.0]$):
  - `low`: $\text{trap}(0.0, 0.0, 0.25, 0.45)$
  - `medium`: $\text{tri}(0.30, 0.55, 0.80)$
  - `high`: $\text{trap}(0.65, 0.85, 1.0, 1.0)$
- **`order_total`** ($[0.0, 1.0]$):
  - `small`: $\text{trap}(0.0, 0.0, 0.25, 0.45)$
  - `medium`: $\text{tri}(0.30, 0.55, 0.80)$
  - `large`: $\text{trap}(0.65, 0.85, 1.0, 1.0)$
- **`uncertainty`** ($[0.0, 1.0]$):
  - `low`: $\text{trap}(0.0, 0.0, 0.25, 0.45)$
  - `medium`: $\text{tri}(0.30, 0.55, 0.80)$
  - `high`: $\text{trap}(0.65, 0.85, 1.0, 1.0)$
- **Output `review_priority`** ($[0.0, 1.0]$):
  - `low`: $\text{trap}(0.0, 0.0, 0.25, 0.45)$
  - `medium`: $\text{tri}(0.35, 0.55, 0.75)$
  - `high`: $\text{trap}(0.65, 0.85, 1.0, 1.0)$

### 3.2. Crossover Thresholds
- $x = 0.40$: Crossover between `low` and `medium`.
- $x = 0.70$: Crossover between `medium` and `high`.

| Score Interval | Linguistic Label |
|---|---|
| $[0.0, 0.40)$ | `low` |
| $[0.40, 0.70)$ | `medium` |
| $[0.70, 1.00]$ | `high` |

---

## 4. Rule Base (81 Rules)

The rule base systematically maps all $3 \times 3 \times 3 \times 3 = 81$ combinations using an additive monotonic score:
$$\text{score}(c, s, t, u) = 3c + 3s + t + u \quad \text{where } c, s, t, u \in \{0, 1, 2\}$$

- $\text{score} \le 3 \implies \text{review\_priority} = \text{low}$
- $4 \le \text{score} \le 7 \implies \text{review\_priority} = \text{medium}$
- $\text{score} \ge 8 \implies \text{review\_priority} = \text{high}$

This guarantees that every single rule consequent is weakly monotonic across every input dimension.

---

## 5. Safety Floors

To ensure domain safety invariants, `base(k, x)` applies minimum score floors:

1. **Critical Discrepancy Risk Floor (`CRITICAL_RISK_FLOOR = 0.75`)**:
   - Applies when $k > 0$ AND (`effective_severity >= 0.80` OR `max_price_dev >= 30.0%`).
   - Guarantees immediate `'high'` priority.
   - Does not apply to clean orders ($k = 0$).

2. **Uncertain SKU Match Floor (`UNCERTAIN_MATCH_FLOOR = 0.45`)**:
   - Applies when `match_confidence is None` OR `match_confidence < 0.40`.
   - Guarantees at least `'medium'` priority (cannot silently auto-approve ambiguous SKU matches).

3. **Incomplete Data Floor (`INCOMPLETE_DATA_FLOOR = 0.45`)**:
   - Applies when critical order data is missing.
   - Guarantees at least `'medium'` priority.

---

## 6. Mathematical Monotonicity Proof

### Theorem
Let $n \in \mathbb{N}_0$, and let $x = (\text{severity}, \text{price\_dev}, \text{total}, \text{confidence})$ be any fixed order profile.
Then:
$$\text{review\_priority}(n + 1, x) \ge \text{review\_priority}(n, x)$$
$$\text{rank}(\text{score\_to\_label}(\text{review\_priority}(n + 1, x))) \ge \text{rank}(\text{score\_to\_label}(\text{review\_priority}(n, x)))$$

### Proof
1. Let $S_n = \{0, \dots, \min(n, \text{COUNT\_CAP})\}$.
2. For $n + 1$, $S_{n+1} = \{0, \dots, \min(n + 1, \text{COUNT\_CAP})\}$.
3. Clearly, $S_n \subseteq S_{n+1}$.
4. By definition of the monotonic upper envelope:
   $$\text{final}(n, x) = \max_{k \in S_n} \text{base}(k, x)$$
   $$\text{final}(n + 1, x) = \max_{k \in S_{n+1}} \text{base}(k, x) = \max\left(\max_{k \in S_n} \text{base}(k, x), \text{base}(\min(n + 1, \text{COUNT\_CAP}), x)\right) \ge \text{final}(n, x)$$
5. The labeling function `score_to_label` is monotonically non-decreasing in score with fixed partition points $0.40$ and $0.70$.
6. Therefore, $\text{rank}(\text{label}(n + 1, x)) \ge \text{rank}(\text{label}(n, x))$. $\blacksquare$

---

## 7. Profile Evaluation Table

Example evaluation across counts $n = 0 \dots 6$ for a representative profile:
- `discrepancy_severity = 0.50` ("Warning")
- `price_deviation_pct = 15.0%`
- `order_total_cents = 100_000` (\$1,000.00)
- `match_confidence = 0.85`

| Count $n$ | Base Score $s_n$ | Monotonic Envelope $\max_{k \le n} s_k$ | Priority Label | Envelope Adjusted |
|---|---|---|---|---|
| $0$ | $0.1785$ | $0.1785$ | `low` | False |
| $1$ | $0.5500$ | $0.5500$ | `medium` | False |
| $2$ | $0.5500$ | $0.5500$ | `medium` | False |
| $3$ | $0.7582$ | $0.7582$ | `high` | False |
| $4$ | $0.8623$ | $0.8623$ | `high` | False |
| $5$ | $0.8623$ | $0.8623$ | `high` | False |
| $6$ | $0.8623$ | $0.8623$ | `high` | False |

---

## 8. Public API & Pipeline Integration

```python
from app.symbolic import (
    DiscrepancyItem,
    OrderReviewInput,
    ReviewPriorityResult,
    evaluate_review_priority,
    review_priority_stage,
)

# Calling with keyword arguments
result = evaluate_review_priority(
    discrepancy_count=2,
    discrepancy_severity="Warning",
    price_deviation_pct=15.0,
    order_total_cents=150_000,
    match_confidence=0.88,
)
print(result.review_priority)       # 0.55
print(result.review_priority_label) # 'medium'

# Calling with structured DiscrepancyItem list
result = evaluate_review_priority(
    discrepancies=[
        DiscrepancyItem(severity="Blocking", price_deviation_pct=35.0),
        DiscrepancyItem(severity="Info", price_deviation_pct=1.0),
    ],
    order_total_cents=50_000,
    match_confidence=0.95,
)
print(result.review_priority)       # >= 0.75
print(result.review_priority_label) # 'high'
```
