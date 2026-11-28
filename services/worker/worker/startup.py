"""Retry the first database connection while a new container's network settles.

A task that Swarm has just started can fail to resolve `postgres` on the overlay network for a few
seconds (socket.gaierror). Exiting then makes Swarm count the rolling update as failed and pause
it, so the process waits for the database instead: up to `timeout` seconds, then it gives up and
lets the restart policy take over.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

import asyncpg

T = TypeVar("T")


async def connect_retrying(
    connect: Callable[[], Awaitable[T]], log: logging.Logger, timeout: float = 60, delay: float = 2,
) -> T:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        try:
            return await connect()
        # gaierror is an OSError; CannotConnectNowError means Postgres is still starting up.
        except (OSError, asyncpg.CannotConnectNowError) as exc:
            if loop.time() + delay > deadline:
                raise
            log.warning("database not reachable yet; retrying", extra={"fields": {"error": repr(exc), "retry_in_s": delay}})
            await asyncio.sleep(delay)
