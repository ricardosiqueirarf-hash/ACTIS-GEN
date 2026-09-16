---
name: excel-spreadsheet
description: Create, inspect, edit and validate Excel .xlsx workbooks with formulas, formatting, tables and charts.
version: 1.0.0
kind: skill-set
inheritable: true
---

# Excel Spreadsheet Skill

Use this skill whenever an `.xlsx` workbook is the primary input or output.

## Operating rules

1. Inspect before editing an existing workbook. Preserve sheets, formulas and layout unless the user explicitly asks to replace them.
2. Use formulas for derived values instead of hard-coding results. Formulas must remain editable by the user after delivery.
3. Prefer a clear workbook structure: inputs/data first, calculations next, summaries/charts last.
4. For tabular data, use headers, sensible widths, freeze panes when useful, and Excel Tables when the data benefits from filtering/sorting.
5. Use number formats appropriate to the data: currency, percentages, dates and decimals should not be stored as presentation-only text.
6. After any significant edit, call `excel_validate_workbook`. When accuracy matters, inspect the changed range as a second verification step.
7. `openpyxl` writes formulas but does not evaluate them. The workbook is marked for recalculation when opened in Excel/LibreOffice; do not pretend cached formula results were recalculated locally.
8. Work only with `.xlsx`. Do not silently convert or rewrite macro-enabled `.xlsm` files.
9. Do not replace the whole workbook when a small surgical edit is enough.

## Available tools

- `excel_create_workbook(path, workbook_json)` — create a new `.xlsx` from structured JSON.
- `excel_inspect_workbook(path, sheet, cell_range, max_rows, max_cols)` — inspect workbook structure, values and formulas.
- `excel_edit_workbook(path, operations_json)` — apply deterministic operations to an existing workbook.
- `excel_validate_workbook(path)` — verify that the workbook opens and summarize structural risks.

## Editing operations

`excel_edit_workbook` accepts a JSON list. Supported operations include:

- `set`: set a single cell value/formula.
- `write_range`: write a matrix starting at a cell.
- `append_rows`: append rows to a sheet.
- `add_sheet`, `rename_sheet`, `delete_sheet`.
- `insert_rows`, `delete_rows`, `insert_cols`, `delete_cols`.
- `merge`, `unmerge`, `freeze_panes`, `auto_filter`.
- `column_width`, `row_height`.
- `style_range`: font, fill, alignment, number format and borders.
- `add_table`: create an Excel Table over a range.
- `add_chart`: bar, line or pie chart from explicit ranges.

## Example edit payload

```json
[
  {"op":"set","sheet":"Resumo","cell":"B2","value":"=SUM(Vendas!D2:D200)"},
  {"op":"style_range","sheet":"Resumo","range":"A1:B2","style":{"bold":true,"fill":"D7FF64","number_format":"R$ #,##0.00"}},
  {"op":"freeze_panes","sheet":"Vendas","cell":"A2"}
]
```

## Verification loop

Create/edit → validate → inspect changed ranges → fix any issue → validate again.

This skill follows the same deterministic, tool-first pattern used by public agent spreadsheet skills in the ecosystem, while keeping execution native to ACTIS GEN rather than depending on an external spreadsheet agent runtime.
