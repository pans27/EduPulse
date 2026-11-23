import json
from datetime import datetime, timezone
from unittest import TestCase

from worker.outbox import kind_for, payload


class OutboxTests(TestCase):
    def test_only_incident_changes_send_email(self):
        self.assertEqual(kind_for("open"), "incident_opened")
        self.assertEqual(kind_for("update"), "incident_updated")
        self.assertEqual(kind_for("resolve"), "incident_resolved")
        # SUSPECT, DEGRADED and first checks change the dashboard but never email anyone.
        self.assertIsNone(kind_for(None))

    def test_payload_describes_the_committed_change(self):
        at = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
        body = json.loads(payload(
            course_id="ece1779", resource_name="lecture5", url="http://fake-site:8000/r/lecture5",
            before="SUSPECT", after="DOWN", detail="HTTP 404", at=at,
        ))
        self.assertEqual(body, {
            "course_id": "ece1779", "resource": "lecture5", "url": "http://fake-site:8000/r/lecture5",
            "previous_state": "SUSPECT", "state": "DOWN", "detail": "HTTP 404", "at": "2026-11-23T12:00:00+00:00",
        })
