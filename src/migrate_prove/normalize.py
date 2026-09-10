from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Any

from migrate_prove.models import NormalizeSpec


def snake_name(name: str) -> str:
    import re

    stepped = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return re.sub(r"\W+", "_", stepped).lower().strip("_")


def canonicalize(value: Any, spec: NormalizeSpec) -> str:
    if value is None:
        return spec.null_token
    if isinstance(value, str) and spec.empty_as_null and value.strip() == "":
        return spec.null_token
    if isinstance(value, datetime):
        return value.strftime(spec.datetime_format)
    if isinstance(value, date):
        return value.strftime(spec.date_format)
    if spec.decimal_places is not None or isinstance(value, Decimal):
        try:
            decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, ValueError):
            text = str(value)
        else:
            if spec.decimal_places is not None:
                quant = Decimal("1").scaleb(-spec.decimal_places)
                decimal_value = decimal_value.quantize(quant, rounding=ROUND_HALF_EVEN)
            text = format(decimal_value, "f")
            if spec.trim:
                text = text.strip()
            if spec.upper:
                text = text.upper()
            return text
    else:
        text = str(value)
    if spec.trim:
        text = text.strip()
    if spec.upper:
        text = text.upper()
    return text
