import os
from datetime import UTC, datetime
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import asyncpg
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.main import app
from app.registry_models import CourseRow, ResourceRow

RESOURCES = "/api/courses/ece1779/resources"


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
        account = Principal(courses={"ece1779": "staff"})
        self.account = self.enterContext(patch.object(app.state.auth, "account", return_value=account))

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
        self.account.return_value = Principal(courses={"ece1779": "student"})
        self.assertEqual(self.client.post(RESOURCES, json={"name": "Lecture", "url": "https://example.com"}).status_code, 403)
        self.session.add.assert_not_called()

    def test_only_course_staff_can_edit_and_delete(self):
        # Reject students and staff from another course before issuing database writes.
        url = f"{RESOURCES}/{uuid4()}"
        for memberships in ({"ece1779": "student"}, {"another-course": "staff"}):
            with self.subTest(memberships=memberships):
                self.account.return_value = Principal(courses=memberships)
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
        # The release time is the only editable field that can be cleared.
        self.assertEqual(self.client.patch(f"{RESOURCES}/{uuid4()}", json={"url": None}).status_code, 422)

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
        self.account.return_value = Principal(courses={}, is_admin=True)
        self.session.scalars.return_value = [CourseRow(id="unassigned", name="Unassigned Course")]
        self.assertIn("Assign user to a course", self.client.get("/dashboard/admin/").text)
        self.assertEqual(self.client.get("/api/admin/courses").json(), [
            {"id": "unassigned", "name": "Unassigned Course"},
        ])
        self.assertEqual(self.client.get("/api/courses").json(), [])
