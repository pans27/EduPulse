import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.websocket_routes import wait_for_course_update


class CourseUpdateRoutingTests(IsolatedAsyncioTestCase):
    async def wait(self, *payloads, course_for_resource=None):
        queue: asyncio.Queue = asyncio.Queue()
        for payload in payloads:
            queue.put_nowait(payload)
        registry = MagicMock()
        registry.course_for_resource = AsyncMock(return_value=course_for_resource)
        await asyncio.wait_for(wait_for_course_update(queue, registry, "ece1779"), 1)
        return queue, registry

    async def test_delete_of_this_course_refreshes_without_a_lookup(self):
        queue, registry = await self.wait("course:ece1779", "never-read")
        registry.course_for_resource.assert_not_awaited()
        self.assertEqual(queue.qsize(), 1)

    async def test_delete_in_another_course_is_ignored(self):
        queue, registry = await self.wait("course:other", "course:ece1779")
        self.assertTrue(queue.empty())
        registry.course_for_resource.assert_not_awaited()

    async def test_resource_ids_are_still_routed_by_lookup(self):
        _, registry = await self.wait(str(uuid4()), course_for_resource="ece1779")
        registry.course_for_resource.assert_awaited_once()
