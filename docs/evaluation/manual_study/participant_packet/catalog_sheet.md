# Product Master Catalog Sheet

This reference document contains the active wholesale product master catalog. All purchase order line items must be matched against these authorized catalog specifications.

## Product Master Catalog

| SKU | Product Name | Unit of Measure | Base Price (USD) | Minimum Order Quantity (MOQ) | Package Increment |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `SKU-WRAP-18` | Industrial Stretch Film 18in 80ga | Roll | $24.50 | 5 | 1 |
| `SKU-WRAP-15` | Standard Pallet Wrap 15in 65ga | Case | $20.00 | 5 | 1 |
| `SKU-TAPE-02` | Heavy Duty Packaging Tape 2in x 110yd | Roll | $3.50 | 6 | 6 |
| `SKU-TAPE-03` | Industrial Filament Strapping Tape 3in | Roll | $8.50 | 4 | 2 |
| `SKU-BOX-MED` | Corrugated Shipping Box 16x12x12in | Bundle | $32.00 | 1 | 1 |
| `SKU-BOX-LRG` | Heavy Duty Corrugated Box 24x18x18in | Bundle | $48.00 | 1 | 1 |
| `SKU-LBL-THERM` | Direct Thermal Shipping Labels 4x6in | Roll | $14.50 | 2 | 2 |
| `SKU-STRAP-POLY` | Polypropylene Strapping Roll 1/2in x 9000ft | Coil | $65.00 | 1 | 1 |
| `SKU-GLOVE-NIT` | Heavy Duty Nitrile Work Gloves Large | Box | $12.00 | 10 | 5 |
| `SKU-VEST-HI` | High Visibility Safety Vest ANSI Class 2 | Each | $9.50 | 5 | 1 |
| `SKU-CUTTER-IND` | Industrial Safety Box Cutter with Holster | Each | $7.25 | 2 | 1 |
| `SKU-BUBBLE-MED` | Perforated Bubble Cushioning Wrap 12in x 175ft | Roll | $28.00 | 1 | 1 |

---

### Packaging Rules Reference
- **Minimum Order Quantity (MOQ)**: An order line quantity must be greater than or equal to the specified MOQ (`quantity >= MOQ`).
- **Package Increment**: An order line quantity must be an exact multiple of the packaging increment (`quantity % package_increment == 0`).
