from datetime import timedelta
from unittest import TestCase

from pydantic import ValidationError

from app.api_models import ReportCreate, ResourceCreate


class ResourceCreateTests(TestCase):
    def test_defaults_and_timezone_aware_release(self):
        # Accept a time with an explicit offset and default to one-minute checks.
        resource = ResourceCreate(
            name="  Lecture notes  ",
            url="https://example.com/notes",
            release_at="2030-01-01T09:00:00-05:00",
        )
        self.assertEqual(resource.name, "Lecture notes")
        self.assertEqual(resource.check_interval_seconds, 60)
        self.assertEqual(resource.release_at.utcoffset(), timedelta(hours=-5))

    def test_invalid_resource_fields(self):
        # Reject inputs that cannot be scheduled or safely represented by the API.
        for overrides, field in (
            ({"name": " "}, "name"),
            ({"url": "ftp://example.com/notes"}, "url"),
            ({"url": "https://user:password@example.com/notes"}, "url"),
            ({"check_interval_seconds": 0}, "check_interval_seconds"),
            ({"release_at": "2030-01-01T09:00:00"}, "release_at"),
            ({"state": "HEALTHY"}, "state"),
        ):
            with self.subTest(field=field, overrides=overrides):
                data = {"name": "Lecture notes", "url": "https://example.com/notes"}
                with self.assertRaises(ValidationError) as raised:
                    ResourceCreate(**(data | overrides))
                self.assertEqual(raised.exception.errors()[0]["loc"], (field,))

    def test_report_description_is_required_and_trimmed(self):
        # Reject blank reports and trim valid descriptions.
        self.assertEqual(ReportCreate(description="  Link is broken  ").description, "Link is broken")
        with self.assertRaises(ValidationError):
            ReportCreate(description="   ")
