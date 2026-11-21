"""EduPulse FastAPI entry point."""

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import literal, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .in_memory_registry import InMemoryRegistry
from .postgres_notifications import PostgresNotifications
from .postgres_registry import PostgresRegistry
from .rest_routes import router as rest_router
from .websocket_routes import router as websocket_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize API storage and release the engine and listener on shutdown.
    use_in_memory_registry = os.getenv("EDUPULSE_LOCAL_API") == "1"
    app.state.registry = InMemoryRegistry() if use_in_memory_registry else None
    database_url = os.getenv("DATABASE_URL") if use_in_memory_registry else os.environ["DATABASE_URL"]
    engine = (
        create_async_engine(
            make_url(database_url).set(drivername="postgresql+asyncpg"),
            pool_size=5,
            max_overflow=0,
            pool_pre_ping=True,
        )
        if database_url
        else None
    )
    app.state.engine = engine
    notification_channel = os.getenv("EDUPULSE_RESOURCE_NOTIFY_CHANNEL") or None
    if not use_in_memory_registry and engine is not None:
        app.state.registry = PostgresRegistry(
            async_sessionmaker(engine), notification_channel, os.getenv("EDUPULSE_DATABASE_WRITES") == "1"
        )
    notifications = (
        PostgresNotifications(database_url, notification_channel)
        if database_url and notification_channel and not use_in_memory_registry
        else None
    )
    app.state.resource_notifications = notifications
    listener_task = asyncio.create_task(notifications.run()) if notifications else None
    try:
        yield
    finally:
        try:
            if listener_task is not None:
                listener_task.cancel()
                with suppress(asyncio.CancelledError):
                    await listener_task
        finally:
            if engine is not None:
                await engine.dispose()


app = FastAPI(title="EduPulse API", lifespan=lifespan)
app.include_router(rest_router)
app.include_router(websocket_router)
app.mount("/dashboard", StaticFiles(directory=Path(__file__).parent / "dashboard", html=True))


@app.exception_handler(DBAPIError)
async def schema_unavailable(_request: Request, error: DBAPIError) -> JSONResponse:
    # Return a clear error if migrations have not been applied.
    if getattr(error.orig, "sqlstate", None) != "42P01":
        raise error
    return JSONResponse(status_code=503, content={"detail": "Apply the EduPulse database migrations"})


@app.get("/health")
async def health(response: Response) -> dict[str, str]:
    # Report API health and check PostgreSQL connectivity when configured.
    if app.state.engine is None:
        return {"status": "ok", "database": "disabled", "mode": "local"}
    try:
        async with app.state.engine.connect() as connection:
            await connection.scalar(select(literal(1)))
        return {"status": "ok", "database": "ok"}
    except (SQLAlchemyError, OSError, TimeoutError):
        response.status_code = 503
        return {"status": "degraded", "database": "unavailable"}
