from pathlib import Path

import pytest
from pydantic import ValidationError

from migrate_prove.models import ConnectionConfig, SuiteConfig
from migrate_prove.secrets import (
    MissingEnvError,
    expand_string,
    redact_url,
    resolve_connection,
)


def test_expand_required_and_default():
    env = {"SOURCE_USER": "migrator", "SOURCE_HOST": "db.internal"}
    assert expand_string("${SOURCE_USER}", env) == "migrator"
    assert expand_string("${SOURCE_PORT:-5432}", env) == "5432"
    assert (
        expand_string(
            "postgresql+psycopg://${SOURCE_USER}@${SOURCE_HOST}:${SOURCE_PORT:-5432}/legacy",
            env,
        )
        == "postgresql+psycopg://migrator@db.internal:5432/legacy"
    )


def test_expand_missing_var_fails_fast():
    with pytest.raises(MissingEnvError, match="SOURCE_PASSWORD"):
        expand_string("x://${SOURCE_PASSWORD}@h/db", {})


def test_structured_connection_builds_url(tmp_path: Path):
    block = ConnectionConfig(
        dialect="postgresql+psycopg",
        host="${SOURCE_HOST}",
        port="${SOURCE_PORT:-5432}",
        database="${SOURCE_DB}",
        username="${SOURCE_USER}",
        password="${SOURCE_PASSWORD}",
        options={"sslmode": "require"},
    )
    url = resolve_connection(
        block,
        tmp_path,
        environ={
            "SOURCE_HOST": "db.internal",
            "SOURCE_DB": "legacy",
            "SOURCE_USER": "migrator",
            "SOURCE_PASSWORD": "s3cret!",
        },
    )
    assert url.startswith("postgresql+psycopg://migrator:")
    assert "db.internal:5432/legacy" in url
    assert "sslmode=require" in url
    assert "s3cret" in url  # encoded password present in resolved URL
    assert "***" in redact_url(url)
    assert "s3cret" not in redact_url(url)


def test_url_form_with_env_and_sqlite_relative(tmp_path: Path):
    db = tmp_path / "source.db"
    db.write_text("", encoding="utf-8")
    url = resolve_connection(
        {"url": "sqlite:///source.db"},
        tmp_path,
        environ={},
    )
    assert url.startswith("sqlite:///")
    assert "source.db" in url


def test_url_xor_structured_rejected():
    with pytest.raises(ValidationError):
        ConnectionConfig(url="sqlite:///x.db", host="localhost")


def test_suite_loads_sqlite_without_env():
    suite = SuiteConfig.model_validate(
        {
            "name": "demo",
            "contract": "stm.yaml",
            "source": {"url": "sqlite:///source.db"},
            "target": {"url": "sqlite:///target.db"},
        }
    )
    assert suite.source.url == "sqlite:///source.db"


def test_suite_loads_salesforce_mock_target():
    suite = SuiteConfig.model_validate(
        {
            "name": "cte-api",
            "contract": "stm.yaml",
            "source": {"url": "sqlite:///source.db"},
            "target": {"kind": "salesforce_mock", "fixtures": "fixtures/salesforce"},
        }
    )
    assert suite.target.kind == "salesforce_mock"
    assert suite.target.fixtures == "fixtures/salesforce"


def test_redact_url_without_password_unchanged():
    plain = "sqlite:///C:/data/source.db"
    assert redact_url(plain) == plain
