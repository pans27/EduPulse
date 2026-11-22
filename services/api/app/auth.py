"""Database accounts, revocable sessions, and course permissions."""

import os
from datetime import timedelta
from hashlib import sha256
from secrets import token_urlsafe
from typing import Annotated, Literal

from anyio import CapacityLimiter, to_thread
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import HTTPConnection

from .registry_models import CourseMemberRow, SessionRow, UserRow

COOKIE = "edupulse_session"
SESSION_SECONDS = 8 * 60 * 60
TOKEN_BYTES = 32
TOKEN_LENGTH = 43
router = APIRouter(prefix="/api/auth")
hasher = PasswordHasher(memory_cost=32768, parallelism=1)


EmailAddress = Annotated[EmailStr, AfterValidator(str.lower), Field(max_length=320)]


class Login(BaseModel):
    email: EmailAddress
    password: str = Field(min_length=1, max_length=256)


class Registration(Login):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=12, max_length=256)


class Principal(BaseModel):
    courses: dict[str, Literal["staff", "student"]]
    is_admin: bool = False


class Auth:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, secure_cookie: bool) -> None:
        # Use PostgreSQL for accounts and session validation.
        self.sessions = sessions
        self.secure_cookie = secure_cookie
        self.login_limiter = CapacityLimiter(1)
        self.fallback_hash = hasher.hash(os.urandom(32))

    def verify(self, password_hash: str, password: str) -> bool:
        # Run the CPU-intensive password verification in a worker thread.
        try:
            return hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    async def sign_in(self, credentials: Login, previous_token: str | None) -> str | None:
        # Verify an active account, then replace the browser's previous session.
        user_id = None
        password_hash = self.fallback_hash
        async with self.sessions() as session:
            user = await session.scalar(
                select(UserRow).where(UserRow.email == credentials.email, UserRow.is_active)
            )
            if user is not None:
                user_id, password_hash = user.id, user.password_hash
        matches = await to_thread.run_sync(
            self.verify, password_hash, credentials.password, limiter=self.login_limiter,
        )
        if user_id is None or not matches:
            return None

        token = token_urlsafe(TOKEN_BYTES)
        previous_hash = session_token_hash(previous_token)
        expired = SessionRow.expires_at <= func.now()
        async with self.sessions.begin() as session:
            stale_sessions = expired if previous_hash is None else or_(
                expired, SessionRow.token_hash == previous_hash
            )
            await session.execute(delete(SessionRow).where(stale_sessions))
            session.add(SessionRow(
                token_hash=sha256(token.encode()).digest(),
                user_id=user_id,
                expires_at=func.now() + timedelta(seconds=SESSION_SECONDS),
            ))
        return token

    async def register(self, credentials: Registration) -> None:
        # Create an unprivileged account and enforce unique email under concurrent requests.
        password_hash = await to_thread.run_sync(hasher.hash, credentials.password, limiter=self.login_limiter)
        async with self.sessions.begin() as session:
            user_id = await session.scalar(
                insert(UserRow).values(email=credentials.email, password_hash=password_hash)
                .on_conflict_do_nothing(index_elements=[UserRow.email])
                .returning(UserRow.id)
            )
            if user_id is None:
                raise HTTPException(409, "An account already uses this email")

    async def account(self, connection: HTTPConnection) -> Principal | None:
        # Resolve an unexpired session cookie to its current account.
        token_hash = session_token_hash(connection.cookies.get(COOKIE))
        if token_hash is None:
            return None
        async with self.sessions() as session:
            rows = (await session.execute(
                select(UserRow.is_admin, CourseMemberRow.course_id, CourseMemberRow.role)
                .select_from(SessionRow)
                .join(UserRow, UserRow.id == SessionRow.user_id)
                .outerjoin(CourseMemberRow, CourseMemberRow.user_id == UserRow.id)
                .where(SessionRow.token_hash == token_hash, SessionRow.expires_at > func.now(), UserRow.is_active)
            )).all()
        if not rows:
            return None
        return Principal(
            is_admin=rows[0].is_admin,
            courses={row.course_id: row.role for row in rows if row.course_id is not None},
        )

    async def revoke(self, token: str | None) -> None:
        # Delete the session so another replica cannot reuse it.
        token_hash = session_token_hash(token)
        if token_hash is not None:
            async with self.sessions.begin() as session:
                await session.execute(delete(SessionRow).where(SessionRow.token_hash == token_hash))


def session_token_hash(token: str | None) -> bytes | None:
    # Hash tokens matching the shape issued by token_urlsafe(32).
    if token is None or len(token) != TOKEN_LENGTH or not token.isascii():
        return None
    return sha256(token.encode()).digest()


async def require_access(
    connection: HTTPConnection, course_id: str | None = None, *, staff: bool = False
) -> Principal:
    # Enforce configured course membership and staff permissions.
    auth: Auth = connection.app.state.auth
    if connection.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
        raise HTTPException(403, "Use the EduPulse dashboard origin")
    account = await auth.account(connection)
    if account is None:
        raise HTTPException(401, "Sign in to continue")
    if course_id is not None:
        role = account.courses.get(course_id)
        if role is None or (staff and role != "staff"):
            raise HTTPException(403, "You do not have access to this action")
    return account


@router.get("/session")
async def session_status(request: Request) -> dict:
    # Tell the dashboard whether the user is signed in and which courses are available.
    auth: Auth = request.app.state.auth
    account = await auth.account(request)
    return {
        "signed_in": account is not None,
        "courses": account.courses if account else {}, "is_admin": account.is_admin if account else False,
    }


@router.post("/login", status_code=204)
async def login(credentials: Login, request: Request, response: Response) -> None:
    # Verify credentials and issue an opaque HTTP-only session cookie.
    auth: Auth = request.app.state.auth
    if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
        raise HTTPException(403, "Use the EduPulse dashboard origin")
    token = await auth.sign_in(credentials, request.cookies.get(COOKIE))
    if token is None:
        raise HTTPException(401, "Invalid email or password")
    response.set_cookie(
        COOKIE, token, max_age=SESSION_SECONDS,
        httponly=True, secure=auth.secure_cookie, samesite="strict",
    )


@router.post("/register", status_code=201)
async def register(credentials: Registration, request: Request) -> dict[str, str]:
    # Allow self-registration without granting admin privileges or course memberships.
    if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
        raise HTTPException(403, "Use the EduPulse dashboard origin")
    await request.app.state.auth.register(credentials)
    return {"email": credentials.email}


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response) -> None:
    # Revoke the session in PostgreSQL and clear the browser cookie.
    auth: Auth = request.app.state.auth
    if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
        raise HTTPException(403, "Use the EduPulse dashboard origin")
    await auth.revoke(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, httponly=True, secure=auth.secure_cookie, samesite="strict")
