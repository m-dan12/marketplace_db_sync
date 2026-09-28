"""Retry/backoff wrapper shared by all marketplace-source HTTP clients.

WB/Ozon/Selsup all return 429/5xx under load — this centralizes the
policy: 429 waits for `Retry-After` (or `x-ratelimit-reset`) rather than
being treated as a fatal error, 5xx and transient network errors get
exponential backoff, and after `max_retries` attempts the underlying
error is raised (never swallowed).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 6
BACKOFF_BASE_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 120.0
DEFAULT_RATE_LIMIT_WAIT_SECONDS = 90.0

_RETRIABLE_NETWORK_ERRORS = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    httpx.TimeoutException,
)


class RetryExhaustedError(RuntimeError):
    """Raised when `max_retries` attempts were all retriable failures."""


def _exponential_wait(attempt: int) -> float:
    return min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)


def _rate_limit_wait(response: httpx.Response) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after is not None:
        try:
            return min(float(retry_after), MAX_BACKOFF_SECONDS)
        except ValueError:
            pass
    reset = response.headers.get("x-ratelimit-reset")
    if reset is not None:
        try:
            return min(float(reset), MAX_BACKOFF_SECONDS)
        except ValueError:
            pass
    return DEFAULT_RATE_LIMIT_WAIT_SECONDS


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    **kwargs: Any,
) -> httpx.Response:
    """`client.request(method, url, **kwargs)` with retry/backoff on
    429/5xx/transient network errors. Raises on any other HTTP error
    status (via `response.raise_for_status()`) or once retries are
    exhausted."""
    last_exc: Exception | None = None
    response: httpx.Response | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.request(method, url, **kwargs)
        except _RETRIABLE_NETWORK_ERRORS as exc:
            last_exc = exc
            wait = _exponential_wait(attempt)
            logger.warning(
                "network error on %s %s (attempt %d/%d): %s, retrying in %.0fs",
                method, url, attempt, max_retries, exc, wait,
            )
            time.sleep(wait)
            continue

        if response.status_code == 429:
            wait = _rate_limit_wait(response)
            logger.warning(
                "rate limited on %s %s (attempt %d/%d), waiting %.0fs",
                method, url, attempt, max_retries, wait,
            )
            time.sleep(wait)
            continue

        if response.status_code >= 500:
            wait = _exponential_wait(attempt)
            logger.warning(
                "server error %d on %s %s (attempt %d/%d), retrying in %.0fs",
                response.status_code, method, url, attempt, max_retries, wait,
            )
            time.sleep(wait)
            continue

        response.raise_for_status()
        return response

    if response is not None:
        response.raise_for_status()
    raise RetryExhaustedError(
        f"{method} {url} failed after {max_retries} attempts"
    ) from last_exc
