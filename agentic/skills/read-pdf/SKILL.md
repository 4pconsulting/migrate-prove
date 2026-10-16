---
name: read-pdf
description: >-
  Extracts text and tables from PDF mapping specifications into migrate-prove
  dossier sections. Use when Collator inputs include PDF files or the user
  mentions PDF mapping docs.
---

# read-pdf

## Goal

Pull entity names, field mappings, lookups, and quirks from PDFs into the dossier — not into a final STM.

## Extraction (Python)

Prefer `pdfplumber` when available:

```powershell
python -m pip install pdfplumber
```

```python
import pdfplumber

path = r"path\to\mapping.pdf"
with pdfplumber.open(path) as pdf:
    for i, page in enumerate(pdf.pages, 1):
        text = page.extract_text() or ""
        tables = page.extract_tables() or []
        print(f"--- page {i} ---")
        print(text)
        for t in tables:
            for row in t:
                print(row)
```

If `pdfplumber` fails on a scanned PDF, note **OCR required** in the dossier open questions; do not invent mappings.

## Map into dossier

| Extracted | Dossier section |
| --- | --- |
| Table/entity titles | Entities |
| Source/target column pairs | Mappings table |
| Code lists (A→Active) | Lookups |
| NULL / trim / length notes | Quirks |
| Contradictions across pages | Conflicts |

## Rules

- Cite page numbers in the Source doc column
- Keep verbatim codes for lookups
- Never invent tolerances from marketing prose
