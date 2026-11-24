import asyncio
from contextlib import suppress
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from app.postgres_notifications import PostgresNotifications


class PostgresNotificationsTests(IsolatedAsyncioTestCase):
    async def test_fanout_and_reconnect(self):
        # Give each subscriber resource IDs and request a resync on reconnect.
        listener = PostgresNotifications("postgresql://unused", "test_changes", retry_delay=0)
        first = Mock()
        second = Mock()
        for connection in (first, second):
            connection.add_listener = AsyncMock()
            connection.is_closed.return_value = False

        with patch("app.postgres_notifications.asyncpg.connect", side_effect=[first, second]):
            async with listener.subscribe() as updates, listener.subscribe() as other:
                task = asyncio.create_task(listener.run())
                try:
                    self.assertIsNone(await asyncio.wait_for(updates.get(), 1))
                    self.assertIsNone(other.get_nowait())
                    channel, callback = first.add_listener.await_args.args
                    self.assertEqual(channel, "test_changes")
                    callback(first, 1, channel, " ")
                    self.assertTrue(updates.empty())
                    callback(first, 1, channel, "resource-42")
                    self.assertEqual(updates.get_nowait(), "resource-42")
                    self.assertEqual(other.get_nowait(), "resource-42")

                    first.is_closed.return_value = True
                    first.add_termination_listener.call_args.args[0](first)
                    self.assertIsNone(await asyncio.wait_for(updates.get(), 1))
                    second.add_listener.assert_awaited_once_with(channel, callback)
                finally:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
        second.terminate.assert_called_once()
        self.assertFalse(listener.subscribers)

    async def test_silent_disconnect_is_detected_by_heartbeat(self):
        # Reconnect when an idle connection stops answering without ever reporting that it closed.
        listener = PostgresNotifications(
            "postgresql://unused", "test_changes", retry_delay=0, heartbeat_interval=0.01, heartbeat_timeout=0.01
        )
        dead = Mock()
        fresh = Mock()
        for connection in (dead, fresh):
            connection.add_listener = AsyncMock()
            connection.is_closed.return_value = False
        dead.fetchval = AsyncMock(side_effect=TimeoutError)
        fresh.fetchval = AsyncMock(return_value=1)

        with patch("app.postgres_notifications.asyncpg.connect", side_effect=[dead, fresh]):
            async with listener.subscribe() as updates:
                task = asyncio.create_task(listener.run())
                try:
                    self.assertIsNone(await asyncio.wait_for(updates.get(), 1))
                    self.assertIsNone(await asyncio.wait_for(updates.get(), 1))  # resync after reconnecting
                    fresh.add_listener.assert_awaited_once()
                    dead.terminate.assert_called_once()
                    dead.fetchval.assert_awaited_with("SELECT 1", timeout=0.01)
                finally:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    async def test_queue_overflow_requests_full_resync(self):
        # Signal a full refresh if a subscriber falls behind.
        listener = PostgresNotifications("postgresql://unused", "test_changes")
        async with listener.subscribe() as updates:
            for index in range(updates.maxsize + 1):
                listener._publish(str(index))
            self.assertIsNone(updates.get_nowait())
            self.assertTrue(updates.empty())
