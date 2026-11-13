"""EduPulse API (placeholder).

Skeleton so the Compose stack runs end to end. Carla replaces this with the
real REST, auth, and WebSocket endpoints; keep /health, which Nginx, Swarm
healthchecks, and the DigitalOcean Uptime Check rely on.
"""
import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Response

DATABASE_URL = os.environ["DATABASE_URL"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    yield
    await app.state.pool.close()


app = FastAPI(title="EduPulse API", lifespan=lifespan)


@app.get("/health")
async def health(response: Response) -> dict:
    try:
        async with app.state.pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "ok", "database": "ok"}
    except Exception as exc:
        response.status_code = 503
        return {"status": "degraded", "database": str(exc)}
