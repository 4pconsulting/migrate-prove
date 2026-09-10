from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote_plus, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv

from migrate_prove.models import ConnectionConfig

_ENV_PATTERN = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}"
)


class MissingEnvError(ValueError):
    """Raised when a required ${VAR} cannot be resolved."""


def load_env(env_file: Path | None = None) -> None:
    """Load `.env` for local runs. Process env (CI secrets) always wins over file values."""
    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=False)
        return
    load_dotenv(override=False)


def expand_string(value: str, environ: Mapping[str, str] | None = None) -> str:
    env = environ if environ is not None else os.environ

    def replacer(match: re.Match[str]) -> str:
        name = match.group("name")
        default = match.group("default")
        if name in env:
            return env[name]
        if default is not None:
            return default
        raise MissingEnvError(
            f"Environment variable {name!r} is not set "
            f"(referenced as ${{{name}}}). Set it in the process environment "
            f"or a local .env file."
        )

    previous = None
    current = value
    # Allow nested / multi-pass expansion without unbounded loops.
    for _ in range(10):
        if current == previous:
            return current
        previous = current
        current = _ENV_PATTERN.sub(replacer, current)
    return current


def expand_value(value: Any, environ: Mapping[str, str] | None = None) -> Any:
    if isinstance(value, str):
        return expand_string(value, environ)
    if isinstance(value, dict):
        return {key: expand_value(item, environ) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_value(item, environ) for item in value]
    return value


def _resolve_sqlite_path(url: str, base: Path) -> str:
    if not url.startswith("sqlite:///"):
        return url
    rest = url.removeprefix("sqlite:///")
    if rest.startswith("/") or (len(rest) > 1 and rest[1] == ":"):
        return url
    return "sqlite:///" + (base / rest).resolve().as_posix()


def build_url_from_structured(config: ConnectionConfig, environ: Mapping[str, str] | None = None) -> str:
    dialect = expand_string(config.dialect or "", environ)
    host = expand_string(config.host or "", environ)
    database = expand_string(config.database or "", environ)
    if not dialect or not host or not database:
        raise ValueError("Structured connections require dialect, host, and database")

    username = expand_string(config.username, environ) if config.username else None
    password = expand_string(config.password, environ) if config.password else None
    port_raw = expand_string(str(config.port), environ) if config.port is not None else None

    userinfo = ""
    if username is not None:
        userinfo = quote_plus(username)
        if password is not None:
            userinfo += f":{quote_plus(password)}"
        userinfo += "@"

    host_part = host
    if port_raw:
        host_part = f"{host}:{port_raw}"

    options = expand_value(config.options or {}, environ)
    query = urlencode({str(k): str(v) for k, v in options.items()}) if options else ""
    return f"{dialect}://{userinfo}{host_part}/{database}" + (f"?{query}" if query else "")


def resolve_connection(
    block: ConnectionConfig | dict[str, Any],
    base: Path,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Turn a suite connection block into a fully resolved SQLAlchemy URL."""
    config = block if isinstance(block, ConnectionConfig) else ConnectionConfig.model_validate(block)
    if config.url is not None:
        url = expand_string(config.url, environ)
        return _resolve_sqlite_path(url, base)
    return build_url_from_structured(config, environ)


def redact_url(url: str) -> str:
    """Mask userinfo password (and opaque credentials) for console/logs."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable-url>"
    if not parts.scheme:
        return url
    # sqlite and similar have no netloc userinfo
    if not parts.netloc or "@" not in parts.netloc:
        return url
    userinfo, _, hostport = parts.netloc.rpartition("@")
    if ":" in userinfo:
        user, _, _password = userinfo.partition(":")
        userinfo = f"{user}:***"
    else:
        userinfo = "***"
    redacted_netloc = f"{userinfo}@{hostport}"
    return urlunsplit((parts.scheme, redacted_netloc, parts.path, parts.query, parts.fragment))
