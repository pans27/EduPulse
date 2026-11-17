import os
from datetime import UTC, datetime, timedelta
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app


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
    def test_course_routes_require_shared_storage(self):
        # Keep preview routes gated when the API runs in its default mode.
        environment = {"EDUPULSE_LOCAL_API": "0", "DATABASE_URL": "postgresql://unused"}
        with patch.dict(os.environ, environment), patch("app.main.asyncpg.create_pool", new_callable=AsyncMock):
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/courses").status_code, 503)
