from datetime import timedelta
from unittest import TestCase

from worker.message import MAX_ATTEMPTS, is_permanent, message_id, render, retry_delay

PAYLOAD = {
    "course_id": "ece1779", "resource": "lecture5", "url": "http://fake-site:8000/r/lecture5",
    "previous_state": "SUSPECT", "state": "DOWN", "detail": "HTTP 404", "at": "2026-11-23T12:00:00+00:00",
}


class MessageTests(TestCase):
    def test_render_opened(self):
        mail = render("abc", "staff@example.com", "incident_opened", PAYLOAD)
        self.assertEqual(mail["message_id"], "<abc@edupulse>")
        self.assertEqual(mail["to"], "staff@example.com")
        self.assertEqual(mail["subject"], "[EduPulse] lecture5 is DOWN")
        self.assertIn("State: SUSPECT -> DOWN", mail["text"])
        self.assertIn("Detail: HTTP 404", mail["text"])

    def test_render_resolved_and_first_check(self):
        body = {**PAYLOAD, "previous_state": None, "state": "HEALTHY"}
        mail = render("abc", "s@example.com", "incident_resolved", body)
        self.assertEqual(mail["subject"], "[EduPulse] lecture5 is back (HEALTHY)")
        self.assertIn("State: unchecked -> HEALTHY", mail["text"])

    def test_same_outbox_row_same_message_id(self):
        # What makes a resend after a crash recognisable as a duplicate.
        self.assertEqual(message_id("x"), render("x", "a@example.com", "incident_opened", PAYLOAD)["message_id"])
        self.assertNotEqual(message_id("x"), message_id("y"))

    def test_backoff_doubles_and_caps(self):
        self.assertEqual([retry_delay(n).total_seconds() for n in (1, 2, 3, 4)], [10, 20, 40, 80])
        self.assertEqual(retry_delay(20), timedelta(minutes=15))
        # Total wait before giving up stays well under an hour.
        total = sum(retry_delay(n).total_seconds() for n in range(1, MAX_ATTEMPTS))
        self.assertLess(total, 3600)

    def test_permanent_vs_retryable(self):
        for status in (400, 404, 413, 422):
            self.assertTrue(is_permanent(status), status)
        # 401/403: a configuration problem (token rotation, wrong key), fixed without a new alert.
        for status in (401, 403, 429, 500, 502, 503):
            self.assertFalse(is_permanent(status), status)
