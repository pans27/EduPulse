"""EduPulse FastAPI entry point."""

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import literal, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .admin_routes import admin_sessions, router as admin_router
from .auth import Auth, router as auth_router
from .postgres_notifications import PostgresNotifications
from .postgres_registry import PostgresRegistry
from .resource_credentials import load_cipher
from .rest_routes import router as rest_router
from .websocket_routes import router as websocket_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize API storage and release the engine and listener on shutdown.
    database_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(
        make_url(database_url).set(drivername="postgresql+asyncpg"),
        pool_size=5,
        max_overflow=0,
        pool_pre_ping=True,
    )
    app.state.engine = engine
    listener_task = None
    try:
        sessions = async_sessionmaker(engine)
        app.state.auth = Auth(sessions, secure_cookie=os.getenv("EDUPULSE_LOCAL_TESTING") != "1")
        notification_channel = os.getenv("EDUPULSE_RESOURCE_NOTIFY_CHANNEL") or None
        app.state.registry = PostgresRegistry(sessions, notification_channel, load_cipher())
        notifications = PostgresNotifications(database_url, notification_channel) if notification_channel else None
        app.state.resource_notifications = notifications
        listener_task = asyncio.create_task(notifications.run()) if notifications else None
        yield
    finally:
        try:
            if listener_task is not None:
                listener_task.cancel()
                with suppress(asyncio.CancelledError):
                    await listener_task
        finally:
            await engine.dispose()


app = FastAPI(title="EduPulse API", lifespan=lifespan)
app.include_router(rest_router)
app.include_router(websocket_router)
app.include_router(auth_router)
app.include_router(admin_router)


@app.exception_handler(RequestValidationError)
async def redact_resource_validation(request: Request, error: RequestValidationError) -> JSONResponse:
    # Validation failures on resource writes must not echo any part of the submitted body.
    route = request.scope.get("route")
    if request.method in {"POST", "PATCH"} and getattr(route, "path", "") in {
        "/api/courses/{course_id}/resources",
        "/api/courses/{course_id}/resources/{resource_id}",
    }:
        return JSONResponse(status_code=422, content={
            "detail": [
                {key: issue[key] for key in ("type", "loc", "msg")}
                for issue in error.errors()
            ],
        })
    return await request_validation_exception_handler(request, error)


@app.get("/dashboard/admin/", include_in_schema=False)
async def admin_page(request: Request) -> Response:
    # Serve the administration page only to a signed-in administrator.
    try:
        await admin_sessions(request)
    except HTTPException as error:
        if error.status_code == 401:
            return RedirectResponse("/dashboard/", status_code=303)
        raise
    return FileResponse(Path(__file__).parent / "admin.html", headers={"Cache-Control": "no-store"})


app.mount("/dashboard", StaticFiles(directory=Path(__file__).parent / "dashboard", html=True))


@app.exception_handler(DBAPIError)
async def schema_unavailable(_request: Request, error: DBAPIError) -> JSONResponse:
    # Return a clear error if migrations have not been applied.
    if getattr(error.orig, "sqlstate", None) != "42P01":
        raise error
    return JSONResponse(status_code=503, content={"detail": "Apply the EduPulse database migrations"})


@app.get("/live")
async def live() -> dict[str, str]:
    # Confirm the API can serve requests without checking external dependencies.
    return {"status": "ok"}


@app.get("/health")
async def health(response: Response) -> dict[str, str]:
    # Report API health and check PostgreSQL connectivity.
    try:
        async with app.state.engine.connect() as connection:
            await connection.scalar(select(literal(1)))
        return {"status": "ok", "database": "ok"}
    except (SQLAlchemyError, OSError, TimeoutError):
        response.status_code = 503
        return {"status": "degraded", "database": "unavailable"}
