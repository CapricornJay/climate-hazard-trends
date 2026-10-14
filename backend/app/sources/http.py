import asyncio
import json
import logging
import random
from typing import Any

import httpx

log = logging.getLogger(__name__)


class UpstreamError(Exception):
    """A third-party API failed. `str(exc)` is safe to show to end users;
    `detail` may contain raw upstream text and is only for logs."""

    status_code = 502

    def __init__(self, message: str, retry_after: int | None = None, detail: str = ""):
        super().__init__(message)
        self.retry_after = retry_after
        self.detail = detail


class UpstreamBusy(UpstreamError):
    """Upstream is rate limiting us, or we'd blow our own budget for it."""

    status_code = 503


class _Retryable(Exception):
    pass


def make_client(user_agent: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0),
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        headers={"User-Agent": user_agent, "Accept": "application/json"},
        follow_redirects=False,
    )


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
    *,
    name: str,
    retries: int = 2,
    timeout: httpx.Timeout | float | None = None,
    max_bytes: int = 40_000_000,
) -> Any:
    """GET + JSON decode with bounded retries.

    Retries only things that can plausibly succeed a second later (timeouts,
    connection errors, 5xx). 4xx means we sent something wrong and 429 means
    "back off", so neither is retried here.
    """
    delay = 1.0
    for attempt in range(retries + 1):
        try:
            kwargs = {"params": params}
            if timeout is not None:
                kwargs["timeout"] = timeout
            async with client.stream("GET", url, **kwargs) as resp:
                if resp.status_code == 429:
                    body = await _read(resp, 4096, truncate=True)
                    raise UpstreamBusy(
                        f"{name} is rate limiting us at the moment",
                        retry_after=_retry_after(resp),
                        detail=body.decode("utf-8", "replace"),
                    )
                if resp.status_code >= 500:
                    raise _Retryable(f"HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    body = await _read(resp, 4096, truncate=True)
                    log.warning("%s rejected %s (%s): %s", name, url, resp.status_code, body[:300])
                    raise UpstreamError(
                        f"{name} rejected the request", detail=body.decode("utf-8", "replace")
                    )
                body = await _read(resp, max_bytes)
        except (httpx.TimeoutException, httpx.TransportError, _Retryable) as exc:
            if attempt == retries:
                raise UpstreamError(f"{name} is not responding right now, try again shortly") from exc
            log.info("%s attempt %d failed (%r), retrying", name, attempt + 1, exc)
            await asyncio.sleep(delay + random.random() / 2)
            delay *= 2
            continue

        try:
            return json.loads(body)
        except ValueError as exc:
            raise UpstreamError(f"{name} returned something that isn't JSON") from exc

    raise AssertionError("unreachable")


async def _read(resp: httpx.Response, limit: int, truncate: bool = False) -> bytes:
    chunks, size = [], 0
    async for chunk in resp.aiter_bytes():
        size += len(chunk)
        if size > limit:
            if truncate:
                chunks.append(chunk[: max(0, limit - (size - len(chunk)))])
                break
            raise UpstreamError("upstream response was unexpectedly large")
        chunks.append(chunk)
    return b"".join(chunks)


def _retry_after(resp: httpx.Response) -> int | None:
    value = resp.headers.get("retry-after", "")
    return int(value) if value.isdigit() else None
