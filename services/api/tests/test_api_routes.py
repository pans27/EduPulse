import os
from datetime import UTC, datetime, timedelta
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
from fastapi.testclient import TestClient
from sqlalchemy.exc import ProgrammingError

from app.main import app
from app.registry_models import CourseRow


RESOURCES = "/api/courses/ece1779/resources"
COURSE_WS = "/api/courses/ece1779/ws"


class PreviewRoutesTests(TestCase):
    def setUp(self):
        # Start an isolated API preview with no database connection.
        self.enterContext(patch.dict(os.environ, {"EDUPULSE_LOCAL_API": "1", "DATABASE_URL": ""}))
        self.client = self.enterContext(TestClient(app))

    def test_registration_and_report_reschedule_resource(self):
        # Keep a future release scheduled until a student requests an immediate recheck.
        release_at = datetime.now(UTC) + timedelta(days=1)
        response = self.client.post(
            RESOURCES,
            json={
                "name": "Lecture notes",
                "url": "https://example.com/notes",
                "release_at": release_at.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        resource = response.json()
        self.assertEqual(datetime.fromisoformat(resource["next_check_at"]), release_at)
        self.assertIsNone(resource["state"])
        self.assertEqual(self.client.get(RESOURCES).json(), [resource])
        self.assertEqual(
            self.client.get(f"/api/courses/demo/resources/{resource['id']}").status_code,
            404,
        )

        resource_path = f"{RESOURCES}/{resource['id']}"
        report = self.client.post(f"{resource_path}/reports", json={"description": "Link is broken"})
        self.assertEqual(report.status_code, 201, report.text)
        self.assertEqual(self.client.get(resource_path).json()["next_check_at"], report.json()["created_at"])
        self.assertEqual(
            self.client.get("/api/metrics").json(),
            {"registered_resources": 1, "student_reports": 1},
        )

    def test_websocket_updates_and_reconnect(self):
        # Send the initial snapshot, then push changes and recover them on reconnect.
        with self.client.websocket_connect(COURSE_WS) as socket:
            self.assertEqual(socket.receive_json()["resources"], [])
            response = self.client.post(
                RESOURCES,
                json={"name": "Lecture notes", "url": "https://example.com/notes"},
            )
            self.assertEqual(response.status_code, 201, response.text)
            updated = socket.receive_json()
            self.assertEqual(updated["type"], "course.snapshot")
            self.assertEqual(updated["resources"], [response.json()])
        with self.client.websocket_connect(COURSE_WS) as socket:
            self.assertEqual(socket.receive_json(), updated)

    def test_health_in_preview_mode(self):
        # Report healthy preview mode even when PostgreSQL is disabled.
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "disabled", "mode": "local"})


class DefaultModeTests(TestCase):
    def setUp(self):
        # Run database mode against a mock ORM session with local-only options disabled.
        environment = {
            "EDUPULSE_LOCAL_API": "0",
            "DATABASE_URL": "postgresql://unused",
            "EDUPULSE_RESOURCE_NOTIFY_CHANNEL": "",
            "EDUPULSE_DATABASE_WRITES": "0",
        }
        self.enterContext(patch.dict(os.environ, environment))
        self.session = AsyncMock()
        self.sessions = MagicMock()
        self.sessions.return_value.__aenter__.return_value = self.session
        self.enterContext(patch("app.main.create_async_engine", return_value=AsyncMock()))
        self.enterContext(patch("app.main.async_sessionmaker", return_value=self.sessions))
        self.client = self.enterContext(TestClient(app))

    def test_course_routes_read_postgresql(self):
        # Return database courses through the same API response shape as preview mode.
        courses = [{"id": "test", "name": "Test Course"}]
        self.session.scalars.return_value = [CourseRow(**course) for course in courses]
        self.assertEqual(self.client.get("/api/courses").json(), courses)
        self.session.scalars.assert_awaited_once()

    def test_missing_schema_returns_service_unavailable(self):
        # Tell local operators to migrate an empty database.
        self.session.scalars.side_effect = ProgrammingError(None, None, asyncpg.UndefinedTableError())
        self.assertEqual(self.client.get("/api/courses").status_code, 503)

    def test_database_mutations_require_opt_in(self):
        # Reject unauthenticated writes in database mode by default.
        response = self.client.post(RESOURCES, json={"name": "Lecture", "url": "https://example.com"})
        self.assertEqual(response.status_code, 403)
        self.sessions.begin.assert_not_called()
