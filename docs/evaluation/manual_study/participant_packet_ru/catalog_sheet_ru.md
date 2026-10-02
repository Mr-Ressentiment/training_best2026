# Справочник товаров (Product Master Catalog)

Этот документ содержит официальный каталог оптовых товаров компании. Все позиции из заказов клиентов необходимо сверять с характеристиками и правилами упаковки из этого каталога.

## Каталог товаров

| SKU | Название товара | Единица измерения | Базовая цена (USD) | Минимальное количество заказа (MOQ) | Шаг упаковки |
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

### Правила проверки количества и упаковки

- **Минимальное количество заказа (MOQ — Minimum Order Quantity)**:
  Минимальное количество единиц товара, разрешенное к заказу для данного SKU. Заказанное количество должно быть не меньше указанного MOQ (`количество >= MOQ`).
- **Шаг упаковки (Package Increment)**:
  Товар отгружается только определенными партиями (упаковками). Заказанное количество единиц должно делиться на шаг упаковки нацело без остатка (`количество % шаг_упаковки == 0`).
