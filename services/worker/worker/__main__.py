"""EduPulse worker (placeholder).

Skeleton so the Compose stack runs end to end: it only proves the worker can
reach PostgreSQL. Byan replaces the loop with lease-based checks. The
dispatcher will later run from this same image with a different command.
"""
import asyncio
import logging
import os

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s worker %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]
HEARTBEAT_SECONDS = int(os.environ.get("WORKER_HEARTBEAT_SECONDS", "10"))


async def main() -> None:
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
    try:
        while True:
            async with pool.acquire() as conn:
                now = await conn.fetchval("SELECT now()")
            log.info("heartbeat: database reachable at %s", now.isoformat())
            await asyncio.sleep(HEARTBEAT_SECONDS)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
