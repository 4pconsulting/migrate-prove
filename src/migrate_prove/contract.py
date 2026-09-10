from __future__ import annotations

from pathlib import Path

import yaml

from migrate_prove.models import Contract, SuiteConfig


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not contain a YAML mapping")
    return data


def load_contract(path: Path) -> Contract:
    return Contract.model_validate(load_yaml(path))


def load_suite(path: Path) -> SuiteConfig:
    return SuiteConfig.model_validate(load_yaml(path))
