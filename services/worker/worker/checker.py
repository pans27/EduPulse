"""Perform one HTTP check and report what was observed (classification happens in classify.py)."""
import asyncio
import time

import httpx

from .classify import Observation, needs_get_fallback

TIMEOUT_SECONDS = 10  # per request
# Whole check (HEAD, GET fallback and every redirect) must finish well inside the 30 s lease, so
# the result is saved while this worker still owns the resource. The worker passes lease - 10 s.
DEADLINE_SECONDS = 20
USER_AGENT = "EduPulse-monitor/1.0 (+course resource availability checks)"


def new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=5,
        timeout=TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
    )


async def _request(client: httpx.AsyncClient, method: str, url: str) -> tuple[int, str, int]:
    # Stream so a GET on a large file (a dataset) stops after the headers.
    started = time.perf_counter()
    async with client.stream(method, url) as response:
        latency_ms = round((time.perf_counter() - started) * 1000)
        return response.status_code, str(response.url), latency_ms


async def check(client: httpx.AsyncClient, url: str, deadline_seconds: float = DEADLINE_SECONDS) -> Observation:
    try:
        async with asyncio.timeout(deadline_seconds):
            return await _check(client, url)
    except TimeoutError:
        # Each request may be under its own timeout while the sum is not (5 redirects + GET retry).
        return Observation(url, error="deadline_exceeded")


async def _check(client: httpx.AsyncClient, url: str) -> Observation:
    try:
        status, final_url, latency_ms = await _request(client, "HEAD", url)
        if needs_get_fallback(status):
            status, final_url, latency_ms = await _request(client, "GET", url)
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
