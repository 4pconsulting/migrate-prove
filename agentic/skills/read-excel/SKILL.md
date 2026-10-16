---
name: read-excel
description: >-
  Extracts sheets and tables from Excel mapping workbooks (.xlsx) into
  migrate-prove dossier sections. Use when Collator inputs include Excel or
  the user mentions mapping spreadsheets.
---

# read-excel

## Goal

Turn workbook sheets (often the real STM draft) into structured dossier mappings and lookups.

## Extraction (Python)

`openpyxl` is available via `migrate-prove[excel]`:

```powershell
python -m pip install "migrate-prove[excel]"
```

```python
from openpyxl import load_workbook

path = r"path\to\mapping.xlsx"
wb = load_workbook(path, data_only=True)
for name in wb.sheetnames:
    ws = wb[name]
    print(f"=== sheet: {name} ===")
    for row in ws.iter_rows(values_only=True):
        if any(cell is not None and str(cell).strip() != "" for cell in row):
            print(row)
```

For `.xls` legacy files, note conversion needed or use a one-off converter; do not invent rows.

## Heuristics

- Sheets named Mapping / STM / Fields → primary mappings table
- Sheets named Lookup / Ref / Codes → lookups
- Header row detection: look for Source, Target, Transform, Type, Nullable
- Multiple entities may be separate sheets or a filter column

## Map into dossier

| Sheet content | Dossier section |
| --- | --- |
| Field rows | Mappings |
| Code tables | Lookups |
| Filter columns | Filters / dedup |
| Comments / notes columns | Quirks or Open questions |

## Rules

- Preserve blank vs NULL distinctions from cells when noted
- Record sheet name + row as source citation
- Conflicts across sheets go to Conflicts — do not merge silently
