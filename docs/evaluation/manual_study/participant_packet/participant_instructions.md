# Participant Instructions: Manual Purchase Order Reconciliation Study

Welcome to the Purchase Order Reconciliation Study. In this study, you will act as a wholesale order reconciliation operator. Your role is to examine incoming customer purchase orders, verify them against our commercial catalog and customer contract pricing schedules, and determine whether each order should be approved or rejected/held for review.

---

## 1. Study Overview & Canonical Order

You will review three separate purchase order cases independently in the following canonical sequence:

1. **Case A**: Source file `docs/evaluation/fixtures/sc001_prepared_5line_po.txt`
2. **Case B**: Source file `tests/fixtures/po_discrepancy_apex.txt`
3. **Case C**: Source file `tests/fixtures/po_ambiguous_apex.txt`

> [!IMPORTANT]
> Complete each case completely and submit its reconciliation template before opening the next case. Do not alter the order of cases.

---

## 2. Permitted & Prohibited Tools

### Permitted Tools
- **Standard desktop or physical calculator** (e.g., Windows Calculator, handheld calculator).
- **Basic spreadsheet software** (e.g., Microsoft Excel, Google Sheets, LibreOffice Calc) used purely as a scratchpad or simple table.
- **Text editor or document viewer** (e.g., Notepad, VS Code, web browser) to view purchase order text files, catalog sheets, and contract pricing sheets.

### Prohibited Tools
- **OrderShield software**: Do not access the OrderShield web application, backend API, or database.
- **AI / LLM tools**: Do not use ChatGPT, Claude, Gemini, Copilot, or any other large language model or AI assistant.
- **Automated scripts**: Do not run automated parsing, reconciliation, or regex comparison scripts.
- **External assistance**: Do not collaborate with or ask questions of other participants or the study observer regarding order details.
- **Answer keys**: Do not inspect test assertions, fixtures, or ground truth files.

---

## 3. Timing Rules

- **Timer Start**: The observer (or your timer) begins timing the exact second you open the purchase order file and associated reference sheets.
- **Timer Stop**: Timing stops the exact second you submit your completed `reconciliation_template.md` for that case.
- Work continuously at your normal operational pace. Do not pause mid-case unless an emergency occurs (which must be noted as a procedural deviation).

---

## 4. Reconciliation Procedure

For each purchase order case, follow these operational steps:

### Step 1: Document Inspection
Open the purchase order text file and note the customer name, account ID, and purchase order number in your session information table.

### Step 2: Line-by-Line Verification
For each line item listed on the purchase order:
1. **Catalog Matching & SKU Uniqueness**:
   - Compare the customer description against the **Product Master Catalog Sheet** (`catalog_sheet.md`).
   - A partial product-name overlap is **not** sufficient for a confident SKU match when the customer description omits distinguishing catalog attributes such as size, gauge, material, package form, or other attributes needed to uniquely distinguish catalog products.
   - You may assign a SKU **only** when the supplied customer description uniquely identifies one catalog item from the available information.
   - If more than one catalog product remains reasonably compatible, or required distinguishing attributes are missing, record **"Needs clarification"**.
2. **Packaging & Contract Verification (Only if SKU is Determinate)**:
   - **If the SKU is determinate (uniquely resolved)**:
     - Check if the requested quantity meets or exceeds the product's Minimum Order Quantity (`quantity >= MOQ`).
     - Check if the requested quantity is an exact multiple of the product's Package Increment (`quantity % package_increment == 0`).
     - Open the **Customer Contract Pricing Sheet** (`contract_pricing_sheet.md`) and locate the section for the customer account.
     - Find the authorized SKU and apply the volume tier rule: select the tier with the maximum `min_quantity` such that `min_quantity <= ordered_quantity`.
     - Verify whether the unit price stated on the purchase order matches the authoritative contract unit price.
     - Compute `quantity * verified unit price` and verify whether it matches the stated line total.
   - **If the SKU is ambiguous (unresolved / "Needs clarification")**:
     - Mark **"Needs clarification"** in the Matched SKU column and record a catalog matching discrepancy.
     - Do **not** guess or arbitrarily choose a SKU.
     - Optional observations about rules common to all candidate products may be noted in the Notes column, but are not required.
3. **Discrepancy Identification**:
   - If any discrepancy is detected (price mismatch, MOQ violation, packaging increment breach, arithmetic error, or ambiguous catalog item requiring clarification), record it clearly in the row's Discrepancy column.

### Step 3: Order Totals & Subtotals
- Calculate the sum of all line totals.
- Verify whether the customer-stated subtotal and total on the purchase order match the calculated sum.

### Step 4: Final Decision
Select your final decision on the reconciliation template:
- **APPROVE**: Select only if all lines match catalog items, quantities comply with MOQ and packaging rules, prices match contract schedules, and all arithmetic is exact.
- **REJECT / NEEDS REVIEW**: Select if any discrepancy is found—such as a price mismatch, MOQ or packaging violation, arithmetic error, or an ambiguous product description that requires customer clarification.
- Provide a brief summary of the reasons for your decision.

### Step 5: Submission
Submit your completed reconciliation form to the observer. The observer will record your stop timestamp. Repeat the process for the next case.
