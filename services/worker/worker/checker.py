"""Perform one HTTP check and report what was observed (classification happens in classify.py)."""
import asyncio
import os
import time
from urllib.parse import urlsplit

import httpx

from .classify import Observation, needs_get_fallback

TIMEOUT_SECONDS = 10  # per request
# Whole check (HEAD, GET fallback and every redirect) must finish well inside the 30 s lease, so
# the result is saved while this worker still owns the resource. The worker passes lease - 10 s.
DEADLINE_SECONDS = 20
USER_AGENT = "EduPulse-monitor/1.0 (+course resource availability checks)"


def new_client(*, trust_env: bool = True) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=5,
        timeout=TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
        trust_env=trust_env,
    )


async def _request(
    client: httpx.AsyncClient, method: str, url: str, api_token: str | None
) -> tuple[int, str, int]:
    # Stream so a GET on a large file (a dataset) stops after the headers.
    started = time.perf_counter()
    options = {} if api_token is None else {
        "headers": {"Authorization": f"Bearer {api_token}"},
        "follow_redirects": False,
    }
    async with client.stream(method, url, **options) as response:
        latency_ms = round((time.perf_counter() - started) * 1000)
        return response.status_code, str(response.url), latency_ms


async def check(
    client: httpx.AsyncClient, url: str, deadline_seconds: float = DEADLINE_SECONDS, *, api_token: str | None = None
) -> Observation:
    # Send credentials over HTTPS, with a local exception for the Compose fixture.
    target = urlsplit(url) if api_token is not None else None
    if target is not None and target.scheme != "https" and not (
        os.getenv("EDUPULSE_LOCAL_TESTING") == "1"
        and target.scheme == "http" and target.netloc == "fake-site:8000"
    ):
        return Observation(url, error="insecure_credential_url")
    try:
        async with asyncio.timeout(deadline_seconds):
            return await _check(client, url, api_token)
    except TimeoutError:
        # Each request may be under its own timeout while the sum is not (5 redirects + GET retry).
        return Observation(url, error="deadline_exceeded")


async def _check(client: httpx.AsyncClient, url: str, api_token: str | None) -> Observation:
    try:
        status, final_url, latency_ms = await _request(client, "HEAD", url, api_token)
        if needs_get_fallback(status):
            status, final_url, latency_ms = await _request(client, "GET", url, api_token)
        return Observation(url, status=status, final_url=final_url, latency_ms=latency_ms)
    except httpx.TimeoutException:
        error = "timeout"
    except httpx.TooManyRedirects:
        error = "too_many_redirects"
    except httpx.ConnectError:
        error = "connect_error"
    except httpx.HTTPError as exc:
        error = type(exc).__name__
    return Observation(url, error=error)
