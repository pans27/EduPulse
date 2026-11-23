"""Turn one HTTP check into an outcome.

Pure functions only, so every rule is unit-tested without a network or database. The HTTP
client in the worker records what it observed (status, final URL after redirects, latency,
or a transport error) and these functions decide what that means.
"""
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

Outcome = Literal["ok", "slow", "auth", "not_found", "error"]

# A successful response slower than this counts as degraded.
SLOW_MS = 2000

# Path segments that mean "the site sent us to sign in". Matched only after a redirect,
# so a resource whose own URL contains "login" is still checked normally.
LOGIN_PATH = re.compile(r"/(log-?in|sign-?in|sso|cas|auth/login)(/|$|\?)", re.IGNORECASE)


@dataclass(frozen=True)
class Observation:
    """What the HTTP client saw. Either status is set, or error is."""

    requested_url: str
    status: int | None = None
    final_url: str | None = None
    latency_ms: int | None = None
    error: str | None = None  # e.g. "timeout", "connect_error"


def needs_get_fallback(head_status: int) -> bool:
    # Some servers refuse HEAD; retry those with GET before judging the resource.
    return head_status in (405, 501)


def redirected_to_login(observation: Observation) -> bool:
    final = observation.final_url
    if not final or final == observation.requested_url:
        return False
    return LOGIN_PATH.search(urlsplit(final).path + "/") is not None


def classify(observation: Observation) -> Outcome:
    if observation.error is not None or observation.status is None:
        return "error"  # timeout, DNS, connection refused, TLS failure, too many redirects
    status = observation.status
    if status in (401, 403) or redirected_to_login(observation):
        # A login redirect usually ends in 200, so the status code alone would call it healthy.
        return "auth"
    if status in (404, 410):
        return "not_found"
    if 200 <= status < 300:
        if observation.latency_ms is not None and observation.latency_ms > SLOW_MS:
            return "slow"
        return "ok"
    # 5xx, 429 and any other unexpected status (including an unfollowed 3xx).
    return "error"
