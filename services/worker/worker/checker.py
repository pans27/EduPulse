"""Perform one HTTP check and report what was observed (classification happens in classify.py)."""
import time

import httpx

from .classify import Observation, needs_get_fallback

TIMEOUT_SECONDS = 10
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


async def check(client: httpx.AsyncClient, url: str) -> Observation:
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
