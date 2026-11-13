"""Fake course site: a controllable test target for CourseWatch.

Each named resource has a mode that decides how /r/{name} responds, so we can
inject failures on demand for demos and evaluation. Modes are held in memory,
which is why this service runs as a single replica.
"""
import asyncio
import os
import random
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel

def _admin_token() -> str:
    # In Swarm the token comes from a Docker secret file; locally from an env var.
    path = os.environ.get("FAKE_ADMIN_TOKEN_FILE")
    if path:
        with open(path) as f:
            return f.read().strip()
    return os.environ.get("FAKE_ADMIN_TOKEN", "dev-token")


ADMIN_TOKEN = _admin_token()

Mode = Literal["ok", "not_found", "server_error", "slow", "flapping", "fail_every_k",
               "login_redirect", "unauthorized", "head_not_allowed"]


class Behavior(BaseModel):
    mode: Mode = "ok"
    delay_ms: int = 3000      # used by "slow"
    fail_rate: float = 0.2    # used by "flapping" (random failures)
    k: int = 5                # used by "fail_every_k" (deterministic failures)


app = FastAPI(title="fake-course-site")
behaviors: dict[str, Behavior] = {
    "lecture5": Behavior(),
    "a1-handout": Behavior(),
    "dataset": Behavior(),
}
hits: dict[str, int] = {}


def require_admin(token: str | None) -> None:
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="bad admin token")


@app.api_route("/r/{name}", methods=["GET", "HEAD"])
async def resource(name: str, request: Request) -> Response:
    b = behaviors.get(name)
    if b is None:
        return PlainTextResponse("no such resource", status_code=404)
    hits[name] = hits.get(name, 0) + 1

    match b.mode:
        case "ok":
            return PlainTextResponse(f"{name}: content")
        case "not_found":
            return PlainTextResponse("not found", status_code=404)
        case "server_error":
            return PlainTextResponse("internal error", status_code=500)
        case "slow":
            await asyncio.sleep(b.delay_ms / 1000)
            return PlainTextResponse(f"{name}: slow content")
        case "flapping":
            if random.random() < b.fail_rate:
                return PlainTextResponse("transient error", status_code=503)
            return PlainTextResponse(f"{name}: content")
        case "fail_every_k":
            # Never two failures in a row: transient noise with zero chance of
            # N consecutive failures, so any DOWN alert would be a false alarm.
            if hits[name] % b.k == 0:
                return PlainTextResponse("transient error", status_code=503)
            return PlainTextResponse(f"{name}: content")
        case "login_redirect":
            return RedirectResponse(f"/login?next=/r/{name}", status_code=302)
        case "unauthorized":
            return PlainTextResponse("login required", status_code=401)
        case "head_not_allowed":
            if request.method == "HEAD":
                return Response(status_code=405)
            return PlainTextResponse(f"{name}: content")


@app.get("/login", response_class=HTMLResponse)
async def login() -> str:
    return "<html><body><form><input name='password' type='password'></form></body></html>"


@app.api_route("/load/{i}", methods=["GET", "HEAD"])
async def load_target(i: int) -> Response:
    """Stateless always-OK targets for the 2,000-resource load test."""
    return PlainTextResponse("ok")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/admin/resources")
async def list_resources(x_admin_token: str | None = Header(None)) -> dict:
    require_admin(x_admin_token)
    return {name: {**b.model_dump(), "hits": hits.get(name, 0)} for name, b in behaviors.items()}


@app.put("/admin/resources/{name}")
async def set_behavior(name: str, b: Behavior, x_admin_token: str | None = Header(None)) -> dict:
    require_admin(x_admin_token)
    behaviors[name] = b
    hits[name] = 0
    return {name: b.model_dump()}
