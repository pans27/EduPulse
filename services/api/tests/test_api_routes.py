import os
from datetime import UTC, datetime, timedelta
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import asyncpg
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.main import app
from app.postgres_registry import PostgresRegistry
from app.registry_models import CourseRow, ResourceRow

RESOURCES = "/api/courses/ece1779/resources"


class RouteTestCase(TestCase):
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


class DatabaseRoutesTests(RouteTestCase):
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


NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)


def stored_resource(**overrides):
    # A resource as the worker left it: checked, DOWN after 3 failures, next check in 10 minutes.
    values = dict(
        id=uuid4(), course_id="ece1779", name="Handout", url="https://example.com/a1",
        check_interval_seconds=600, release_at=None, next_check_at=NOW + timedelta(minutes=10),
        state="DOWN", consecutive_failures=3, recheck_requested=False,
        lease_owner="worker-1", lease_until=NOW + timedelta(seconds=20), lease_token=uuid4(),
    )
    return ResourceRow(**(values | overrides))


class ResourceEditDeleteTests(RouteTestCase):
    def setUp(self):
        super().setUp()
        self.session.execute.return_value = MagicMock(scalar_one=lambda: NOW)

    def path(self, resource_id):
        return f"{RESOURCES}/{resource_id}"

    def test_students_cannot_edit_or_delete(self):
        self.account.return_value = Principal(courses={"ece1779": "student"})
        resource_id = uuid4()
        self.assertEqual(self.client.patch(self.path(resource_id), json={"name": "x"}).status_code, 403)
        self.assertEqual(self.client.delete(self.path(resource_id)).status_code, 403)
        self.session.scalar.assert_not_called()

    def test_other_course_staff_cannot_edit(self):
        self.account.return_value = Principal(courses={"other": "staff"})
        self.assertEqual(self.client.patch(self.path(uuid4()), json={"name": "x"}).status_code, 403)

    def test_edit_needs_a_field_and_rejects_nulls_and_unknown_fields(self):
        for body in ({}, {"name": None}, {"url": None}, {"check_interval_seconds": None}, {"state": "HEALTHY"},
                     {"url": "https://user:pw@example.com"}):
            self.assertEqual(self.client.patch(self.path(uuid4()), json=body).status_code, 422, body)
        self.session.scalar.assert_not_called()

    def test_missing_resource_is_404(self):
        self.session.scalar.return_value = None
        self.assertEqual(self.client.patch(self.path(uuid4()), json={"name": "x"}).status_code, 404)
        self.assertEqual(self.client.delete(self.path(uuid4())).status_code, 404)

    def test_rename_keeps_state_and_schedule(self):
        resource = stored_resource()
        self.session.scalar.return_value = resource
        response = self.client.patch(self.path(resource.id), json={"name": "  Assignment 1  "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "Assignment 1")
        self.assertEqual((resource.state, resource.consecutive_failures), ("DOWN", 3))
        self.assertEqual(resource.next_check_at, NOW + timedelta(minutes=10))
        self.assertIsNotNone(resource.lease_token)

    def test_new_url_starts_over_and_discards_an_in_flight_check(self):
        resource = stored_resource()
        self.session.scalar.return_value = resource
        response = self.client.patch(self.path(resource.id), json={"url": "https://example.com/a1-v2"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(resource.url, "https://example.com/a1-v2")
        self.assertIsNone(resource.state)
        self.assertEqual(resource.consecutive_failures, 0)
        self.assertEqual(resource.next_check_at, NOW)
        self.assertEqual((resource.lease_owner, resource.lease_until, resource.lease_token), (None, None, None))
        statements = [str(call.args[0]) for call in self.session.execute.await_args_list]
        self.assertTrue(any("UPDATE incidents SET resolved_at" in s for s in statements))

    def test_same_url_is_not_a_change(self):
        resource = stored_resource()
        self.session.scalar.return_value = resource
        self.client.patch(self.path(resource.id), json={"url": "https://example.com/a1"})
        self.assertEqual(resource.state, "DOWN")

    def test_shorter_interval_takes_effect_now_longer_does_not_postpone(self):
        resource = stored_resource()
        self.session.scalar.return_value = resource
        self.client.patch(self.path(resource.id), json={"check_interval_seconds": 30})
        self.assertEqual(resource.next_check_at, NOW + timedelta(seconds=30))
        resource = stored_resource()
        self.session.scalar.return_value = resource
        self.client.patch(self.path(resource.id), json={"check_interval_seconds": 3600})
        self.assertEqual(resource.next_check_at, NOW + timedelta(minutes=10))

    def test_release_time_gates_the_next_check_and_can_be_cleared(self):
        release = NOW + timedelta(days=2)
        resource = stored_resource()
        self.session.scalar.return_value = resource
        self.client.patch(self.path(resource.id), json={"release_at": release.isoformat()})
        self.assertEqual(resource.next_check_at, release)
        self.client.patch(self.path(resource.id), json={"release_at": None})
        self.assertIsNone(resource.release_at)
        self.assertEqual(resource.next_check_at, NOW)

    def test_duplicate_url_on_edit_is_conflict(self):
        driver_error = asyncpg.UniqueViolationError()
        driver_error.constraint_name = "uq_resources_course_url"
        adapter_error = Exception("unique violation")
        adapter_error.__cause__ = driver_error
        resource = stored_resource()
        self.session.scalar.return_value = resource
        self.session.flush.side_effect = IntegrityError(None, None, adapter_error)
        self.assertEqual(self.client.patch(self.path(resource.id), json={"url": "https://example.com/b"}).status_code, 409)

    def test_delete_returns_no_content(self):
        resource_id = uuid4()
        self.session.scalar.return_value = resource_id
        response = self.client.delete(self.path(resource_id))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")


class DeleteNotificationTests(IsolatedAsyncioTestCase):
    async def test_delete_notifies_the_course_because_the_row_is_gone(self):
        session = MagicMock(spec=AsyncSession)
        session.scalar.return_value = uuid4()
        sessions = MagicMock()
        sessions.begin.return_value.__aenter__.return_value = session
        await PostgresRegistry(sessions, "resource_updates").delete("ece1779", uuid4())
        notify = session.execute.await_args.args[0]
        self.assertIn("course:ece1779", str(notify.compile(compile_kwargs={"literal_binds": True})))
