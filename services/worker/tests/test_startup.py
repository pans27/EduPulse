import asyncio
import logging
import socket
from unittest import IsolatedAsyncioTestCase

from worker.startup import connect_retrying

log = logging.getLogger("test")


class ConnectRetryingTests(IsolatedAsyncioTestCase):
    async def test_retries_until_the_name_resolves(self):
        attempts = []

        async def connect():
            attempts.append(1)
            if len(attempts) < 3:
                raise socket.gaierror(-2, "Name or service not known")
            return "pool"

        self.assertEqual(await connect_retrying(connect, log, timeout=1, delay=0.01), "pool")
        self.assertEqual(len(attempts), 3)

    async def test_gives_up_after_the_timeout(self):
        async def connect():
            raise ConnectionRefusedError()

        with self.assertRaises(ConnectionRefusedError):
            await connect_retrying(connect, log, timeout=0.05, delay=0.02)

    async def test_other_errors_are_not_retried(self):
        attempts = []

        async def connect():
            attempts.append(1)
            raise ValueError("bad DSN")

        with self.assertRaises(ValueError):
            await connect_retrying(connect, log, timeout=1, delay=0.01)
        self.assertEqual(len(attempts), 1)
