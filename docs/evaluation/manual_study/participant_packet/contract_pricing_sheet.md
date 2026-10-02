# Customer Contract Pricing Sheet

This reference document contains the authorized customer contract agreements, contracted SKUs, volume discount tiers, and unit prices.

All unit prices must be verified against the active contract tier for the specific customer account and requested quantity.

---

## Contract Volume Tier Selection Rule

For a given customer account and authorized SKU:
1. Verify the customer account and contract validity.
2. Locate the volume tier schedule for the matched SKU.
3. Select the tier with the maximum `min_quantity` such that `min_quantity <= ordered_quantity`.
4. If no tier satisfies `min_quantity <= ordered_quantity` (e.g., `ordered_quantity < 1`), no contract price tier applies.
5. Contract prices are firm. Do **not** apply automatic fallback to catalog base price or default discounts.

---

## Customer Agreements & Pricing Schedules

### 1. Acme Industrial Supplies
- **Customer ID**: `CUST-ACME`
- **Contract ID**: `CONTRACT-ACME-2026`
- **Contract Period**: 2026-01-01 to 2026-12-31

#### Authorized SKUs & Tier Pricing

| SKU | Product Description | Tier ID | Minimum Quantity (`min_quantity`) | Contract Unit Price (USD) |
| :--- | :--- | :--- | :--- | :--- |
| `SKU-WRAP-18` | Industrial Stretch Film 18in 80ga | `TIER-ACME-WRAP18-Q1` | 1 | $26.00 |
| `SKU-WRAP-18` | Industrial Stretch Film 18in 80ga | `TIER-ACME-WRAP18-Q10` | 10 | $25.00 |
| `SKU-WRAP-18` | Industrial Stretch Film 18in 80ga | `TIER-ACME-WRAP18-Q50` | 50 | $23.00 |
| `SKU-WRAP-15` | Standard Pallet Wrap 15in 65ga | `TIER-ACME-WRAP15-Q1` | 1 | $21.00 |
| `SKU-WRAP-15` | Standard Pallet Wrap 15in 65ga | `TIER-ACME-WRAP15-Q5` | 5 | $20.00 |
| `SKU-WRAP-15` | Standard Pallet Wrap 15in 65ga | `TIER-ACME-WRAP15-Q25` | 25 | $18.50 |

---

### 2. Apex Distribution
- **Customer ID**: `CUST-APEX`
- **Contract ID**: `CONTRACT-APEX-2026`
- **Contract Period**: 2026-01-01 to 2026-12-31

#### Authorized SKUs & Tier Pricing

| SKU | Product Description | Tier ID | Minimum Quantity (`min_quantity`) | Contract Unit Price (USD) |
| :--- | :--- | :--- | :--- | :--- |
| `SKU-WRAP-18` | Industrial Stretch Film 18in 80ga | `TIER-APEX-WRAP18-Q1` | 1 | $24.00 |
| `SKU-WRAP-18` | Industrial Stretch Film 18in 80ga | `TIER-APEX-WRAP18-Q10` | 10 | $22.00 |
| `SKU-WRAP-18` | Industrial Stretch Film 18in 80ga | `TIER-APEX-WRAP18-Q50` | 50 | $20.50 |
| `SKU-WRAP-15` | Standard Pallet Wrap 15in 65ga | `TIER-APEX-WRAP15-Q1` | 1 | $20.00 |
| `SKU-WRAP-15` | Standard Pallet Wrap 15in 65ga | `TIER-APEX-WRAP15-Q20` | 20 | $18.50 |
