import os
from datetime import UTC, datetime
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import asyncpg
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.main import app
from app.registry_models import CourseMemberRow, CourseRow, CredentialRow, ResourceRow

RESOURCES = "/api/courses/ece1779/resources"
EMAIL_PREFERENCE = "/api/courses/ece1779/email-preference"


class DatabaseRoutesTests(TestCase):
    def setUp(self):
        # Start database mode with a mocked ORM session.
        environment = {
            "DATABASE_URL": "postgresql://unused",
            "EDUPULSE_RESOURCE_NOTIFY_CHANNEL": "",
        }
        self.enterContext(patch.dict(os.environ, environment))
        self.session = MagicMock(spec=AsyncSession)
        sessions = MagicMock()
        sessions.return_value.__aenter__.return_value = self.session
        sessions.begin.return_value.__aenter__.return_value = self.session
        self.engine = AsyncMock()
        self.enterContext(patch("app.main.create_async_engine", return_value=self.engine))
        self.enterContext(patch("app.main.async_sessionmaker", return_value=sessions))
        self.client = self.enterContext(TestClient(app))
        account = Principal(user_id=uuid4(), courses={"ece1779": "staff"})
        self.account = self.enterContext(patch.object(app.state.auth, "account", return_value=account))

    def test_student_can_read_and_change_own_email_preference(self):
        user_id = uuid4()
        self.account.return_value = Principal(user_id=user_id, courses={"ece1779": "student"})
        self.session.get.return_value = CourseMemberRow(role="student", email_opt_in=False)
        self.assertEqual(self.client.get(EMAIL_PREFERENCE).json(), {"email_opt_in": False})
        self.session.get.assert_awaited_once_with(CourseMemberRow, (user_id, "ece1779"))
        self.session.scalar.return_value = user_id
        for enabled in (True, False):
            response = self.client.put(EMAIL_PREFERENCE, json={"email_opt_in": enabled})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"email_opt_in": enabled})
            params = self.session.scalar.await_args.args[0].compile().params.values()
            for expected in (user_id, "ece1779", "student", enabled):
                self.assertIn(expected, params)

    def test_staff_receive_email_and_cannot_opt_out(self):
        self.session.get.return_value = CourseMemberRow(role="staff", email_opt_in=False)
        self.assertEqual(self.client.get(EMAIL_PREFERENCE).json(), {"email_opt_in": True})
        self.assertEqual(self.client.put(EMAIL_PREFERENCE, json={"email_opt_in": False}).status_code, 403)
        self.account.return_value = Principal(user_id=uuid4(), courses={})
        self.assertEqual(self.client.get(EMAIL_PREFERENCE).status_code, 403)
        self.assertEqual(self.client.put(EMAIL_PREFERENCE, json={"email_opt_in": True}).status_code, 403)
        self.session.scalar.assert_not_called()

    def test_course_routes_filter_to_memberships(self):
        # Return only courses assigned to the signed-in user.
        self.session.scalars.return_value = [
            CourseRow(id="ece1779", name="ECE1779"), CourseRow(id="other", name="Other")
        ]
        self.assertEqual(self.client.get("/api/courses").json(), [{"id": "ece1779", "name": "ECE1779"}])

    def test_liveness_stays_healthy_during_database_outage(self):
        # Keep container liveness independent of the database health probe.
        with patch.object(self.engine, "connect", new=MagicMock(side_effect=OSError)) as connect:
            self.assertEqual(self.client.get("/live").status_code, 200)
            connect.assert_not_called()
            self.assertEqual(self.client.get("/health").status_code, 503)
            connect.assert_called_once()

    def test_missing_schema_returns_service_unavailable(self):
        # Tell local operators to migrate an empty database.
        self.session.scalars.side_effect = ProgrammingError(None, None, asyncpg.UndefinedTableError())
        self.assertEqual(self.client.get("/api/courses").status_code, 503)

    def test_postgresql_duplicate_url_returns_conflict(self):
        # Exercise the driver exception chain returned by a duplicate insert.
        driver_error = asyncpg.UniqueViolationError()
        driver_error.constraint_name = "uq_resources_course_url"
        adapter_error = Exception("unique violation")
        adapter_error.__cause__ = driver_error
        self.session.get.return_value = CourseRow(id="ece1779", name="Test Course")
        self.session.execute.return_value = MagicMock(scalar_one=lambda: datetime.now(UTC))
        self.session.flush.side_effect = IntegrityError(None, None, adapter_error)
        response = self.client.post(RESOURCES, json={"name": "Lecture", "url": "https://example.com"})
        self.assertEqual(response.status_code, 409)

    def test_anonymous_reads_and_writes_are_rejected(self):
        # Never serve course data or process writes without an authenticated account.
        self.account.return_value = None
        self.assertEqual(self.client.get("/api/courses").status_code, 401)
        self.assertEqual(self.client.post(RESOURCES, json={"name": "Lecture", "url": "https://example.com"}).status_code, 401)
        self.session.scalars.assert_not_called()

    def test_student_cannot_register_resources(self):
        # Restrict course registration to staff even with a valid student session.
        self.account.return_value = Principal(user_id=uuid4(), courses={"ece1779": "student"})
        self.assertEqual(self.client.post(RESOURCES, json={"name": "Lecture", "url": "https://example.com"}).status_code, 403)
        self.session.add.assert_not_called()

    def test_only_course_staff_can_edit_and_delete(self):
        # Reject students and staff from another course before issuing database writes.
        url = f"{RESOURCES}/{uuid4()}"
        for memberships in ({"ece1779": "student"}, {"another-course": "staff"}):
            with self.subTest(memberships=memberships):
                self.account.return_value = Principal(user_id=uuid4(), courses=memberships)
                self.assertEqual(self.client.patch(url, json={"name": "Updated"}).status_code, 403)
                self.assertEqual(self.client.delete(url).status_code, 403)
        self.session.scalar.assert_not_called()

    def test_patch_name_preserves_the_monitoring_schedule(self):
        # Update only the supplied field and keep the URL, schedule and state.
        now = datetime.now(UTC)
        resource = ResourceRow(
            id=uuid4(), course_id="ece1779", name="Lecture", url="https://example.com/lecture",
            check_interval_seconds=60, next_check_at=now, state="HEALTHY",
        )
        self.session.scalar.return_value = resource
        response = self.client.patch(f"{RESOURCES}/{resource.id}", json={"name": "New title"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["name"], "New title")
        self.assertEqual(resource.next_check_at, now)
        self.assertEqual(resource.state, "HEALTHY")
        self.assertEqual(resource.url, "https://example.com/lecture")
        self.session.scalar.assert_awaited_once()

    def test_patch_rejects_null_url(self):
        # A registered resource must always have a URL.
        self.assertEqual(self.client.patch(f"{RESOURCES}/{uuid4()}", json={"url": None}).status_code, 422)

    def test_register_stores_only_encrypted_token(self):
        # Store ciphertext scoped to the resource and keep the token out of responses.
        cipher = Fernet(Fernet.generate_key())
        app.state.registry.cipher = cipher
        self.session.get.return_value = CourseRow(id="ece1779", name="Test Course")
        self.session.execute.return_value = MagicMock(scalar_one=lambda: datetime.now(UTC))
        self.session.flush.side_effect = lambda: setattr(self.session.add.call_args.args[0], "id", uuid4())
        token = "example-resource-token"
        response = self.client.post(RESOURCES, json={
            "name": "Lecture", "url": "https://site.example.com/lecture", "api_token": token,
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertNotIn("api_token", response.json())
        self.assertNotIn(token, response.text)
        resource, credential = [call.args[0] for call in self.session.add.call_args_list]
        self.assertIsInstance(credential, CredentialRow)
        self.assertEqual(credential.resource_id, resource.id)
        self.assertNotIn(token.encode(), credential.token_ciphertext)
        self.assertEqual(cipher.decrypt(credential.token_ciphertext),
                         resource.id.bytes + resource.url.encode() + b"\x00" + token.encode())

    def test_resource_validation_does_not_echo_submitted_token(self):
        # Redact bad input both inside and outside the token field.
        token = "sensitive value"
        for payload in ({"api_token": token}, {"api_token": {"value": token}}):
            response = self.client.post(RESOURCES, json=payload)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(token, response.text)
            self.assertTrue(all("input" not in issue for issue in response.json()["detail"]))

    def test_token_write_requires_https_and_key(self):
        # Refuse to persist credentials for HTTP targets or without encryption configured.
        self.session.get.return_value = CourseRow(id="ece1779", name="Test Course")
        self.session.execute.return_value = MagicMock(scalar_one=lambda: datetime.now(UTC))
        for scheme, status in (("http", 422), ("https", 503)):
            response = self.client.post(RESOURCES, json={
                "name": "Lecture", "url": f"{scheme}://site.example.com/lecture", "api_token": "demo",
            })
            self.assertEqual(response.status_code, status, response.text)

    def test_patch_replaces_and_clears_token(self):
        # Rotate a saved credential, fence the old check, then clear the credential.
        cipher = Fernet(Fernet.generate_key())
        app.state.registry.cipher = cipher
        now = datetime.now(UTC)
        resource = ResourceRow(
            id=uuid4(), course_id="ece1779", name="Lecture", url="https://site.example.com/lecture",
            check_interval_seconds=60, next_check_at=now, state="AUTH_REQUIRED", lease_token=uuid4(),
        )
        credential = CredentialRow(resource_id=resource.id, token_ciphertext=b"old")
        self.session.scalar.side_effect = [resource, now, resource, now]
        self.session.get.return_value = credential
        path = f"{RESOURCES}/{resource.id}"
        response = self.client.patch(path, json={"api_token": "replacement"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("api_token", response.json())
        self.assertTrue(cipher.decrypt(credential.token_ciphertext).endswith(b"\x00replacement"))
        self.assertIsNone(resource.lease_token)
        self.assertIsNone(resource.state)
        response = self.client.patch(path, json={"url": "https://other.example.com/lecture"})
        self.assertEqual(response.status_code, 200, response.text)
        self.session.delete.assert_awaited_once_with(credential)

    def test_staff_cannot_grant_admin(self):
        # Course staff cannot grant global roles.
        response = self.client.put("/api/admin/users/role", json={
            "email": "student@example.com", "role": "admin",
        })
        self.assertEqual(response.status_code, 403)
        self.session.scalar.assert_not_called()

    def test_admin_can_open_administration_and_list_unassigned_courses(self):
        # Restrict Administration to admins and list courses outside their memberships.
        self.assertEqual(self.client.get("/dashboard/admin/").status_code, 403)
        self.assertEqual(self.client.get("/api/admin/courses").status_code, 403)
        self.account.return_value = Principal(user_id=uuid4(), courses={}, is_admin=True)
        self.session.scalars.return_value = [CourseRow(id="unassigned", name="Unassigned Course")]
        self.assertIn("Assign user to a course", self.client.get("/dashboard/admin/").text)
        self.assertEqual(self.client.get("/api/admin/courses").json(), [
            {"id": "unassigned", "name": "Unassigned Course"},
        ])
        self.assertEqual(self.client.get("/api/courses").json(), [])
