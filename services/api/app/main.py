"""EduPulse FastAPI entry point."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles

from .in_memory_registry import InMemoryRegistry
from .rest_routes import router as rest_router
from .websocket_routes import router as websocket_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize API state at startup and close the database pool on shutdown.
    local = os.getenv("EDUPULSE_LOCAL_API") == "1"
    app.state.registry = InMemoryRegistry() if local else None
    database_url = os.getenv("DATABASE_URL") if local else os.environ["DATABASE_URL"]
    app.state.pool = (
        await asyncpg.create_pool(database_url, min_size=1, max_size=5)
        if database_url
        else None
    )
    try:
        yield
    finally:
        if app.state.pool is not None:
            await app.state.pool.close()


app = FastAPI(title="EduPulse API", lifespan=lifespan)
app.include_router(rest_router)
app.include_router(websocket_router)
app.mount("/dashboard", StaticFiles(directory=Path(__file__).parent / "dashboard", html=True))


@app.get("/health")
async def health(response: Response) -> dict[str, str]:
    # Report API health and check PostgreSQL connectivity when configured.
    if app.state.pool is None:
        return {"status": "ok", "database": "disabled", "mode": "local"}
    try:
        async with app.state.pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "ok", "database": "ok"}
    except (asyncpg.PostgresError, OSError, TimeoutError):
        response.status_code = 503
        return {"status": "degraded", "database": "unavailable"}
