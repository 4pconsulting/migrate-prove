---
name: read-word
description: >-
  Extracts text and tables from Word (.docx) mapping documents into
  migrate-prove dossier sections. Use when Collator inputs include Word specs
  or the user mentions .docx mapping documents.
---

# read-word

## Goal

Extract narrative rules and tables from Word specs into the dossier.

## Extraction (Python)

```powershell
python -m pip install python-docx
```

```python
from docx import Document

path = r"path\to\spec.docx"
doc = Document(path)

for p in doc.paragraphs:
    text = (p.text or "").strip()
    if text:
        print(text)

for ti, table in enumerate(doc.tables, 1):
    print(f"=== table {ti} ===")
    for row in table.rows:
        print([cell.text.strip() for cell in row.cells])
```

Legacy `.doc` (not docx): note conversion required; do not guess binary content.

## Map into dossier

| Content | Dossier section |
| --- | --- |
| “Customer maps to …” prose | Mappings (with lower confidence) |
| Formal tables | Mappings / Lookups |
| Business rules / SLA | Invariants mentioned or Open questions |
| Exceptions / known issues | Quirks / Conflicts |

## Rules

- Prefer tables over narrative when both exist; if they disagree → Conflicts
- Quote rule text that will become `when`/`then` later
- Do not author final STM YAML here — Collator dossier only
