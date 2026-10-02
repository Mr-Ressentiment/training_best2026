# Fuzzy SKU Matching Confidence & Ambiguity Specification

## 1. Overview

The `app.symbolic.sku_confidence` module implements a deterministic, explainable evaluation of SKU candidate matching confidence. It combines a 3-input Mamdani fuzzy inference stage (computing individual `candidate_score`) with a dedicated selection ambiguity post-processor (computing `match_confidence` and linguistic decision label `low` / `medium` / `high`).

The module operates strictly on scalar inputs without external database, network, or AI provider dependencies.

---

## 2. Input Features & Universe

Each candidate SKU provides up to three normalized features strictly in $[0.0, 1.0]$:

| Feature | Universe | Semantic Description |
|---|---|---|
| `description_similarity` | $[0.0, 1.0]$ | Semantic/lexical agreement between purchase order description and catalog product. $0$ = unrelated, $1$ = exact match. |
| `price_closeness` | $[0.0, 1.0]$ | Proximity of customer-stated unit price to contract pricing tier or catalog base price. $0$ = large deviation, $1$ = exact match. |
| `quantity_plausibility` | $[0.0, 1.0]$ | Compliance with catalog MOQ and packaging increment multiples. $0$ = completely implausible, $1$ = fully compliant. |

### Conservative Incomplete Data Policy
- `None` is never treated as $1.0$ and cannot artificially inflate confidence.
- Missing `description_similarity` is imputed as $0.0$ and capped at `LOW_CEILING` ($0.35$), ensuring that absence of description similarity **never** leads to `medium` or `high`.
- Missing `price_closeness` or `quantity_plausibility` is conservatively imputed as $0.0$.
- Any selected candidate with missing inputs receives downgrade reason `insufficient_data`.
- Values outside $[0.0, 1.0]$, `NaN`, and `infinity` raise immediate `ValueError`.

---

## 3. Membership Functions

All fuzzy variables operate on the universe $[0.0, 1.0]$ using deterministic trapezoidal (`trap`) and triangular (`tri`) functions:

### 3.1. Input Variables (`description_similarity`, `price_closeness`, `quantity_plausibility`)
- **`low`**: `trap(0.0, 0.0, 0.25, 0.45)` (Left shoulder: 1.0 on $[0.0, 0.25]$, ramp down to 0.0 at $0.45$)
- **`medium`**: `tri(0.30, 0.55, 0.80)` (Triangle: ramp up from $0.30$, peak at $0.55$, ramp down to $0.80$)
- **`high`**: `trap(0.65, 0.85, 1.0, 1.0)` (Right shoulder: ramp up from $0.65$, 1.0 on $[0.85, 1.0]$)

### 3.2. Output Variable (`candidate_score`)
- **`low`**: `trap(0.0, 0.0, 0.25, 0.45)`
- **`medium`**: `tri(0.35, 0.55, 0.75)`
- **`high`**: `trap(0.65, 0.85, 1.0, 1.0)`

### 3.3. Decision Boundaries & Crossover Thresholds
The dominant membership functions cross over at exact thresholds:
- $x = 0.40$: Crossover between `low` and `medium` ($\mu_{low}(0.40) = \mu_{medium}(0.40) = 0.25$)
- $x = 0.70$: Crossover between `medium` and `high` ($\mu_{medium}(0.70) = \mu_{high}(0.70) = 0.25$)

| Score Interval | Linguistic Label | Dominant Fuzzy Set |
|---|---|---|
| $[0.0, 0.40)$ | `low` | `low` |
| $[0.40, 0.70)$ | `medium` | `medium` |
| $[0.70, 1.00]$ | `high` | `high` |

---

## 4. Fuzzy Rule Base (27 Rules)

Inference uses min-conjunction (T-norm: $T(u, v) = \min(u, v)$) and max-aggregation (S-norm: $S(u, v) = \max(u, v)$):

| Rule ID | `description_similarity` | `price_closeness` | `quantity_plausibility` | Output `candidate_score` | Rationale |
|---|---|---|---|---|---|
| `SKU_L_L_L` | `low` | `low` | `low` | `low` | Low description anchor: always low |
| `SKU_L_L_M` | `low` | `low` | `medium` | `low` | Low description anchor: always low |
| `SKU_L_L_H` | `low` | `low` | `high` | `low` | Low description anchor: always low |
| `SKU_L_M_L` | `low` | `medium` | `low` | `low` | Low description anchor: always low |
| `SKU_L_M_M` | `low` | `medium` | `medium` | `low` | Low description anchor: always low |
| `SKU_L_M_H` | `low` | `medium` | `high` | `low` | Low description anchor: always low |
| `SKU_L_H_L` | `low` | `high` | `low` | `low` | Low description anchor: always low |
| `SKU_L_H_M` | `low` | `high` | `medium` | `low` | Low description anchor: always low |
| `SKU_L_H_H` | `low` | `high` | `high` | `low` | Low description anchor: always low |
| `SKU_M_L_L` | `medium` | `low` | `low` | `low` | Moderate description with poor price & qty |
| `SKU_M_L_M` | `medium` | `low` | `medium` | `low` | Moderate description with poor price |
| `SKU_M_L_H` | `medium` | `low` | `high` | `low` | Moderate description with poor price |
| `SKU_M_M_L` | `medium` | `medium` | `low` | `low` | Moderate description with poor qty |
| `SKU_M_M_M` | `medium` | `medium` | `medium` | `medium` | Moderate agreement across all features |
| `SKU_M_M_H` | `medium` | `medium` | `high` | `medium` | Moderate agreement |
| `SKU_M_H_L` | `medium` | `high` | `low` | `low` | Moderate description with poor qty |
| `SKU_M_H_M` | `medium` | `high` | `medium` | `medium` | Moderate description with strong price |
| `SKU_M_H_H` | `medium` | `high` | `high` | `medium` | Moderate description anchored |
| `SKU_H_L_L` | `high` | `low` | `low` | `low` | Strong description negated by poor price & qty |
| `SKU_H_L_M` | `high` | `low` | `medium` | `low` | Strong description negated by poor price |
| `SKU_H_L_H` | `high` | `low` | `high` | `medium` | Strong description downgraded by poor price |
| `SKU_H_M_L` | `high` | `medium` | `low` | `low` | Strong description negated by poor qty |
| `SKU_H_M_M` | `high` | `medium` | `medium` | `medium` | Strong description with moderate price & qty |
| `SKU_H_M_H` | `high` | `medium` | `high` | `high` | Strong description and qty with moderate price |
| `SKU_H_H_L` | `high` | `high` | `low` | `medium` | Strong description & price downgraded by poor qty |
| `SKU_H_H_M` | `high` | `high` | `medium` | `high` | Strong description & price with moderate qty |
| `SKU_H_H_H` | `high` | `high` | `high` | `high` | Full agreement: all features high |

---

## 5. Defuzzification Method

Defuzzification uses standard discrete centroid (Center of Gravity / COG) calculation:
$$x^* = \frac{\sum_{i=0}^N x_i \cdot \mu_{\text{agg}}(x_i)}{\sum_{i=0}^N \mu_{\text{agg}}(x_i)}$$
with discrete resolution $N = 400$ across $[0.0, 1.0]$. If the aggregated area is zero, defaults to the midpoint $0.5$.

---

## 6. Selection Ambiguity Post-Processing

Individual candidate scores ($S_i$) reflect feature quality for each candidate in isolation. Final selection confidence `match_confidence` accounts for competitor separation:

1. Candidates are sorted descending by `candidate_score`:
   - $S_{\text{best}} = S_1$
   - $S_{\text{runner}} = S_2$ (when $|candidates| \ge 2$)
   - $M = S_{\text{best}} - S_{\text{runner}}$ (`score_margin`)
2. **Ambiguity Gate**:
   - If $|candidates| < 2$: $M = \text{None}$, no competition.
   - If $M < \tau_{\text{amb}}$ (default $\tau_{\text{amb}} = 0.15$):
     $$\text{match\_confidence} = \text{round}\left(S_{\text{best}} \cdot \frac{M}{\tau_{\text{amb}}} \cdot \text{LOW\_CEILING}, 4\right)$$
     where $\text{LOW\_CEILING} = 0.35$.
     Because $\text{match\_confidence} \le 0.35 < 0.40$, the decision label is strictly guaranteed to be `low` with `reason='ambiguity'`.
   - If $M \ge \tau_{\text{amb}}$:
     $$\text{match\_confidence} = S_{\text{best}}$$
     The decision label directly reflects the strength of $S_{\text{best}}$ (`high`, `medium`, or `low`).

### Diagnostic Downgrade Reasons
- `none`: Winner is strong ($S_{\text{best}} \ge 0.70$) with clear competitor separation ($M \ge 0.15$ or no competitors).
- `ambiguity`: Top two candidates are close ($M < 0.15$), forcing confidence into `low`.
- `insufficient_data`: Best candidate evaluated with missing features.
- `weak_candidate`: Best candidate has poor individual feature agreement ($S_{\text{best}} < 0.40$).
- `moderate_fit`: Best candidate has moderate individual feature agreement ($0.40 \le S_{\text{best}} < 0.70$).
- `no_candidates`: Candidate list is empty ($0.0$, `low`).

---

## 7. Model Limitations

1. **Deterministic Heuristic**: The output `match_confidence` is a bounded heuristic score $[0.0, 1.0]$ based on expert fuzzy rules; it is **not** a calibrated posterior Bayesian probability $P(SKU = s \mid X)$.
2. **Feature Dependency**: The evaluation assumes pre-normalized feature inputs $[0.0, 1.0]$. Quality of confidence depends on the calibration of the upstream similarity metrics.
3. **Diagnostic Only**: A `high` confidence score does not bypass operator review or automatic policy gates; it acts as advisory evidence for candidate ranking.
