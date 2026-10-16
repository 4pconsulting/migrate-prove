"""Shared httpx helpers for live API connectors."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

DEFAULT_TIMEOUT = 60.0
DEFAULT_RETRIES = 3
_RETRY_STATUSES = {429, 500, 502, 503, 504}


def require_httpx():
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "HTTP/Salesforce connectors require the 'http' extra. "
            "Install with: pip install 'migrate-prove[http]' or 'migrate-prove[salesforce]'"
        ) from exc
    return httpx


def redact_url_for_error(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable-url>"
    if not parts.scheme:
        return url
    netloc = parts.netloc
    if "@" in netloc:
        _, _, hostport = netloc.rpartition("@")
        netloc = hostport
    return f"{parts.scheme}://{netloc}{parts.path}"


def request_json(
    client: Any,
    method: str,
    url: str,
    *,
    retries: int = DEFAULT_RETRIES,
    **kwargs: Any,
) -> Any:
    """Perform an HTTP request returning JSON, with limited retries on 429/5xx."""
    httpx = require_httpx()
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = client.request(method, url, **kwargs)
            if response.status_code in _RETRY_STATUSES and attempt < retries:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 0.5 * (2**attempt)
                time.sleep(min(delay, 10.0))
                continue
            if response.status_code >= 400:
                body = (response.text or "")[:300]
                raise httpx.HTTPStatusError(
                    f"HTTP {response.status_code} for {redact_url_for_error(url)}: {body}",
                    request=response.request,
                    response=response,
                )
            if not response.content:
                return None
            return response.json()
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt >= retries:
                raise RuntimeError(
                    f"HTTP transport error for {redact_url_for_error(url)}: {exc}"
                ) from exc
            time.sleep(0.5 * (2**attempt))
    if last_exc:
        raise last_exc
    raise RuntimeError(f"HTTP request failed for {redact_url_for_error(url)}")
