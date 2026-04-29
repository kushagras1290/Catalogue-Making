# GemPundit Magento Catalog Automation

Production-grade starter code for converting raw loose-gemstone catalogue data into Magento-ready import rows.

## What it does

- Reads raw product rows from an Excel workbook.
- Reads normalized master lookup sheets from the reference workbook.
- Resolves gemstone, gem type, colour, treatment, certification sticker, shipping text, HSN, tax class, URL key, and astro status.
- Converts ratti/carat automatically.
- Generates a Magento import workbook and a validation report.
- Blocks dangerous silent failures by marking rows as `Manual Review`.

## Install

```bash
cd gempundit_catalog_automation_code
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

## Run

Use the generated Excel structure workbook as reference and input:

```bash
python -m catalog_automation.cli \
  --reference ../GemPundit_Magento_Catalog_Automation_Structure.xlsx \
  --input ../GemPundit_Magento_Catalog_Automation_Structure.xlsx \
  --input-sheet 01_Input_Raw_Products \
  --output ./output/magento_import.xlsx
```

To read raw product rows from Google Sheets instead of an input workbook:

```bash
python -m catalog_automation.cli \
  --reference ../GemPundit_Magento_Catalog_Automation_Structure.xlsx \
  --input-source google-sheet \
  --google-sheet-id YOUR_SPREADSHEET_ID \
  --google-sheet-range "'01_Input_Raw_Products'!A:ZZ" \
  --google-service-account-file C:\secure\catalog-sheets-reader.json \
  --output ./output/magento_import.xlsx
```

For a public/shareable sheet, use `--google-auth-mode public` and omit the service account file.

## Recommended production flow

1. Maintain master lookup sheets in the reference workbook.
2. Paste raw product data into `01_Input_Raw_Products`, provide a separate vendor workbook, or read the raw rows from Google Sheets.
3. Run the CLI.
4. Review `Validation_Report`.
5. Fix lookup/master data, not the generated output.
6. Upload only rows with `validation_status = Ready`.

## Hard rules

- SKU must be present and unique.
- Price must be positive.
- `special_price` cannot exceed `price`.
- Missing `gem_type`, `j_colour`, `url_key`, `classification`, `hsn_code`, or `tax_class_id` becomes manual review.
- `gemstone2` is a default fallback, not final truth.
