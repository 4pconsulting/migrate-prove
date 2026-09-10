from __future__ import annotations

import hashlib

from migrate_prove.models import NormalizeSpec
from migrate_prove.normalize import canonicalize


def row_signature(values: list[object], specs: list[NormalizeSpec] | None = None) -> str:
    parts: list[str] = []
    for index, value in enumerate(values):
        spec = specs[index] if specs else NormalizeSpec()
        parts.append(canonicalize(value, spec))
    payload = "|".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
