"""Receive PostgreSQL resource notifications on a dedicated API connection."""

import asyncio
import logging

import asyncpg

logger = logging.getLogger(__name__)


class NotificationSubscription:
    def __init__(self, subscribers: set[asyncio.Queue[str | None]]) -> None:
        # Keep the subscriber set and allocate a bounded queue for this subscription.
        self._subscribers = subscribers
        self._queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=128)

    async def __aenter__(self) -> asyncio.Queue[str | None]:
        # Register the queue so it receives resource updates and resync signals.
        self._subscribers.add(self._queue)
        return self._queue

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        # Unregister the queue when the subscriber exits.
        self._subscribers.discard(self._queue)


class PostgresNotifications:
    def __init__(self, database_url: str, channel: str, retry_delay: float = 2) -> None:
        # Keep connection settings and a bounded queue per local subscriber.
        self.database_url = database_url
        self.channel = channel
        self.retry_delay = retry_delay
        self.subscribers: set[asyncio.Queue[str | None]] = set()

    def subscribe(self) -> NotificationSubscription:
        # Create a subscription where a resource ID requests a refresh and None requests a full resync.
        return NotificationSubscription(self.subscribers)

    def _publish(self, resource_id: str | None) -> None:
        # Fan out an update or collapse a backlog into a full resync.
        for queue in self.subscribers:
            if resource_id is None or queue.full():
                while not queue.empty():
                    queue.get_nowait()
                    queue.task_done()
                queue.put_nowait(None)
            else:
                queue.put_nowait(resource_id)

    def _receive(self, connection: asyncpg.Connection, pid: int, channel: str, payload: str) -> None:
        # Forward the opaque resource ID without assuming a database ID type.
        if payload.strip():
            self._publish(payload)

    async def run(self) -> None:
        # Subscribe, reconnect after connection loss, and release the connection on shutdown.
        while True:
            connection = None
            try:
                disconnected = asyncio.Event()
                connection = await asyncpg.connect(self.database_url, timeout=5)
                connection.add_termination_listener(lambda _: disconnected.set())
                await connection.add_listener(self.channel, self._receive)
                self._publish(None)
                logger.info("Listening for PostgreSQL resource updates on %s", self.channel)
                await disconnected.wait()
                logger.warning("PostgreSQL resource listener connection closed")
            except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
                logger.warning("PostgreSQL resource listener disconnected (%s)", type(exc).__name__)
            finally:
                if connection is not None and not connection.is_closed():
                    connection.terminate()
            await asyncio.sleep(self.retry_delay)
