import os
from hashlib import sha256
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect

from app.auth import COOKIE, hasher
from app.main import app
from app.registry_models import UserRow


class DatabaseAuthTests(TestCase):
    def setUp(self):
        # Mock PostgreSQL account queries and session writes.
        self.enterContext(patch.dict(os.environ, {
            "DATABASE_URL": "postgresql://unused",
            "EDUPULSE_RESOURCE_NOTIFY_CHANNEL": "",
            "EDUPULSE_LOCAL_TESTING": "0",
        }))
        session = MagicMock(spec=AsyncSession)
        sessions = MagicMock()
        sessions.return_value.__aenter__.return_value = session
        self.write_session = MagicMock(spec=AsyncSession)
        sessions.begin.return_value.__aenter__.return_value = self.write_session
        self.enterContext(patch("app.main.create_async_engine", return_value=AsyncMock()))
        self.enterContext(patch("app.main.async_sessionmaker", return_value=sessions))
        self.client = self.enterContext(TestClient(app, base_url="https://testserver"))
        session.scalar.return_value = UserRow(id=uuid4(), password_hash=hasher.hash("example-test-password"))
        self.memberships = MagicMock()
        session.execute.return_value = self.memberships
        self.memberships.all.return_value = [SimpleNamespace(is_admin=False, course_id="demo", role="student")]

    def test_login_and_account_status(self):
        # Verify a password, issue a secure cookie, and reject a session for a disabled user.
        url = "/api/auth/login"
        self.assertEqual(self.client.post(url, json={"email": "student@example.com", "password": "wrong"}).status_code, 401)
        response = self.client.post(url, json={"email": "Student@EXAMPLE.COM", "password": "example-test-password"})
        self.assertEqual(response.status_code, 204, response.text)
        for flag in ("Secure", "HttpOnly", "SameSite=strict", "Max-Age=28800"):
            self.assertIn(flag, response.headers["set-cookie"])
        token = self.client.cookies[COOKIE]
        persisted_session = self.write_session.add.call_args.args[0]
        self.assertEqual(persisted_session.token_hash, sha256(token.encode()).digest())
        self.assertEqual(self.client.get("/api/auth/session").json()["courses"], {"demo": "student"})
        self.memberships.all.return_value = []
        self.assertEqual(self.client.get("/api/courses").status_code, 401)

    def test_anonymous_websocket_is_denied(self):
        # Deny live course snapshots when the handshake has no session.
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("wss://testserver/api/courses/demo/ws"):
                pass

    def test_logout_revokes_session(self):
        # Revoke the persisted token and expire the browser cookie.
        self.client.cookies.set(COOKIE, "x" * 43)
        response = self.client.post("/api/auth/logout")
        self.assertEqual(response.status_code, 204)
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        statement = self.write_session.execute.await_args.args[0].compile()
        self.assertIn(sha256(("x" * 43).encode()).digest(), statement.params.values())

    def test_registration_normalizes_email_and_rejects_role_injection(self):
        # Public account creation never accepts an admin field.
        credentials = {"email": "Student@EXAMPLE.COM", "password": "example-test-password"}
        self.assertEqual(self.client.post("/api/auth/register", json=credentials | {"is_admin": True}).status_code, 422)
        self.write_session.scalar.return_value = uuid4()
        response = self.client.post("/api/auth/register", json=credentials)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json(), {"email": "student@example.com"})

    def test_duplicate_registration_returns_conflict(self):
        # A second registration for the same canonical email is rejected.
        self.write_session.scalar.return_value = None
        response = self.client.post("/api/auth/register", json={
            "email": "student@example.com", "password": "example-test-password",
        })
        self.assertEqual(response.status_code, 409)
