from asyncio import Event
from datetime import UTC, datetime
from unittest import TestCase
from uuid import uuid4

from fastapi import HTTPException

from app.api_models import Resource
from app.in_memory_registry import InMemoryRegistry


class InMemoryRegistryTests(TestCase):
    def test_publish_updates_only_the_matching_course(self):
        # Save updates in place and wake listeners for the resource's course.
        registry = InMemoryRegistry()
        matching_listener, other_listener = Event(), Event()
        registry.listeners["ece1779"].add(matching_listener)
        registry.listeners["demo"].add(other_listener)
        resource = Resource(
            id=uuid4(),
            course_id="ece1779",
            name="Lecture notes",
            url="https://example.com/notes",
            next_check_at=datetime.now(UTC),
        )
        registry.publish(resource)
        matching_listener.clear()

        updated = resource.model_copy(update={"state": "HEALTHY"})
        registry.publish(updated)
        self.assertTrue(matching_listener.is_set())
        self.assertFalse(other_listener.is_set())
        self.assertEqual(registry.resource("ece1779", resource.id), updated)
        self.assertEqual(registry.snapshot("ece1779").resources, [updated])
        self.assertEqual(registry.snapshot("demo").resources, [])
        with self.assertRaises(HTTPException) as raised:
            registry.resource("demo", resource.id)
        self.assertEqual(raised.exception.status_code, 404)

    def test_snapshot_rejects_unknown_course(self):
        # Return a 404 when a snapshot is requested for an unknown course.
        with self.assertRaises(HTTPException) as raised:
            InMemoryRegistry().snapshot("unknown")
        self.assertEqual(raised.exception.status_code, 404)
