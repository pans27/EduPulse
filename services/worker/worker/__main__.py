"""EduPulse check worker.

Loop: claim up to BATCH due resources with a lease, check them concurrently over HTTP, and
record each result in its own transaction (check result, state, incident, NOTIFY). Any number
of replicas can run: SKIP LOCKED keeps them from claiming the same resource, and an expired
lease lets another replica retry a resource whose worker died mid-check.
"""
import asyncio
import os
import signal
import socket

import asyncpg

from . import logs, store
from .leases import work_deadline
from .checker import check, new_client
from .classify import classify
from .startup import connect_retrying

DATABASE_URL = os.environ["DATABASE_URL"]
NOTIFY_CHANNEL = os.environ.get("EDUPULSE_RESOURCE_NOTIFY_CHANNEL", "resource_updates") or None
BATCH = int(os.environ.get("WORKER_BATCH", "10"))
LEASE_SECONDS = int(os.environ.get("WORKER_LEASE_SECONDS", "30"))
POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "2"))
WORKER_ID = os.environ.get("WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}"
# The whole HTTP check must end 10 s before the lease does; a lease too short for that is rejected
# at startup instead of silently allowing checks that outlive it.
CHECK_DEADLINE_SECONDS = work_deadline(LEASE_SECONDS, name="WORKER_LEASE_SECONDS")


log = logs.setup("worker", WORKER_ID)


async def check_one(pool: asyncpg.Pool, client, item: store.Claim) -> None:
    observation = await check(client, item.url, CHECK_DEADLINE_SECONDS)
    outcome = classify(observation)
    recorded = await store.record(pool, item, observation, outcome, NOTIFY_CHANNEL)
    fields = {"resource": str(item.resource_id), "outcome": outcome, "status": observation.status,
              "latency_ms": observation.latency_ms, "error": observation.error}
    if recorded is None:
        log.warning("lease lost; result discarded", extra={"fields": fields})
        return
    fields |= {"state": recorded.after.state, "failures": recorded.after.consecutive_failures}
    if recorded.after.changed(recorded.before):
        fields |= {"from": recorded.before, "incident": recorded.after.incident, "emails_queued": recorded.emails_queued}
        log.info("state changed", extra={"fields": fields})
    else:
        log.info("checked", extra={"fields": fields})


async def run(stop: asyncio.Event) -> None:
    pool = await connect_retrying(lambda: asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=BATCH + 1), log)
    log.info("started", extra={"fields": {"batch": BATCH, "lease_seconds": LEASE_SECONDS}})
    async with new_client() as client:
        try:
            while not stop.is_set():
                try:
                    claimed = await store.claim(pool, WORKER_ID, BATCH, LEASE_SECONDS)
                except (OSError, asyncpg.PostgresError) as exc:
                    # Database restart or failover: keep the process alive and retry.
                    log.warning("claim failed", extra={"fields": {"error": repr(exc)}})
                    claimed = []
                if claimed:
                    results = await asyncio.gather(
                        *(check_one(pool, client, item) for item in claimed), return_exceptions=True
                    )
                    for item, result in zip(claimed, results):
                        if isinstance(result, Exception):
                            # Unrecorded: the lease expires and another worker (or this one) retries.
                            log.error("record failed", extra={"fields": {"resource": str(item.resource_id), "error": repr(result)}})
                    if len(claimed) == BATCH:
                        continue  # more may be due; claim again right away
                try:
                    await asyncio.wait_for(stop.wait(), POLL_SECONDS)
                except TimeoutError:
                    pass
        finally:
            await pool.close()
            log.info("stopped")


def main() -> None:
    stop = asyncio.Event()

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        # Swarm sends SIGTERM on updates and scale-down: finish the current batch, then exit,
        # so a rolling update does not leave leases to expire.
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run(stop)

    asyncio.run(runner())


if __name__ == "__main__":
    main()
