"""EduPulse notification dispatcher: deliver notification_outbox rows to the mail endpoint.

Run with `python -m worker.dispatch` from the worker image. Same pattern as the check worker:
claim pending rows with a lease (FOR UPDATE SKIP LOCKED), send outside any transaction, then
mark each row sent only if this dispatcher still holds its lease.

Delivery is at-least-once. If the dispatcher dies after the mail endpoint accepted a message but
before the row is marked sent, the lease expires and the row is sent again, with the same
Message-ID (the outbox id), so the duplicate can be recognised. The mock sink drops it; a real
mailbox such as Gmail shows both copies.
"""
import asyncio
import json
import os
import signal
import socket

import asyncpg
import httpx

from . import logs
from .leases import work_deadline
from .message import MAX_ATTEMPTS, is_permanent, render, retry_delay
from .outbox import OUTBOX_CHANNEL
from .startup import connect_retrying

DATABASE_URL = os.environ["DATABASE_URL"]
MAIL_ENDPOINT = os.environ["MAIL_ENDPOINT"]  # mock sink now; the DigitalOcean Function later
MAIL_TOKEN_FILE = os.environ.get("MAIL_TOKEN_FILE")
BATCH = int(os.environ.get("DISPATCH_BATCH", "20"))
LEASE_SECONDS = int(os.environ.get("DISPATCH_LEASE_SECONDS", "30"))
POLL_SECONDS = float(os.environ.get("DISPATCH_POLL_SECONDS", "5"))
# Fault injection for the crash test only: exit right after the Nth accepted send, before the
# row is marked sent. Unset in normal runs.
CRASH_AFTER_SEND = int(os.environ.get("DISPATCH_CRASH_AFTER_SEND", "0"))
INSTANCE = os.environ.get("DISPATCH_ID") or f"{socket.gethostname()}:{os.getpid()}"
# Each send is bounded so it ends 10 s before the lease does (rejects a lease shorter than 15 s).
SEND_TIMEOUT_SECONDS = min(10, work_deadline(LEASE_SECONDS, name="DISPATCH_LEASE_SECONDS"))

log = logs.setup("dispatcher", INSTANCE)

CLAIM_SQL = """
WITH due AS (
    SELECT id FROM notification_outbox
    WHERE status = 'pending' AND next_attempt_at <= now() AND (lease_until IS NULL OR lease_until < now())
    ORDER BY next_attempt_at
    LIMIT $1
    FOR UPDATE SKIP LOCKED
)
UPDATE notification_outbox AS o
SET lease_owner = $2, lease_until = now() + make_interval(secs => $3), lease_token = gen_random_uuid(),
    attempts = o.attempts + 1
FROM due
WHERE o.id = due.id
RETURNING o.id, o.recipient_email, o.kind, o.payload, o.attempts, o.lease_token
"""

# Both updates are fenced by lease_token: a dispatcher whose lease expired cannot overwrite the
# outcome recorded by the one that took over.
MARK_SENT_SQL = """
UPDATE notification_outbox
SET status = 'sent', sent_at = now(), last_error = NULL, lease_owner = NULL, lease_until = NULL, lease_token = NULL
WHERE id = $1 AND lease_token = $2
"""
MARK_FAILED_SQL = """
UPDATE notification_outbox
SET status = CASE WHEN $3 OR attempts >= $4 THEN 'failed' ELSE 'pending' END,
    next_attempt_at = now() + make_interval(secs => $5), last_error = $6,
    lease_owner = NULL, lease_until = NULL, lease_token = NULL
WHERE id = $1 AND lease_token = $2
RETURNING status
"""


class Dispatcher:
    def __init__(self, pool: asyncpg.Pool, client: httpx.AsyncClient) -> None:
        self.pool, self.client, self.sent = pool, client, 0

    async def deliver(self, row: asyncpg.Record) -> None:
        mail = render(str(row["id"]), row["recipient_email"], row["kind"], json.loads(row["payload"]))
        fields = {"outbox_id": str(row["id"]), "to": row["recipient_email"], "kind": row["kind"], "attempt": row["attempts"]}
        try:
            response = await self.client.post(MAIL_ENDPOINT, json=mail)
            status, error = response.status_code, None if response.is_success else f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            status, error = None, type(exc).__name__

        if error is None:
            self.sent += 1
            if CRASH_AFTER_SEND and self.sent >= CRASH_AFTER_SEND:
                log.warning("crash injected after send, before marking sent", extra={"fields": fields})
                os._exit(1)  # no cleanup, like a killed container
            done = await self.pool.execute(MARK_SENT_SQL, row["id"], row["lease_token"])
            log.info("sent" if done.endswith("1") else "sent; lease lost, row left to its new owner", extra={"fields": fields})
            return

        permanent = status is not None and is_permanent(status)
        delay = retry_delay(row["attempts"]).total_seconds()
        outcome = await self.pool.fetchval(
            MARK_FAILED_SQL, row["id"], row["lease_token"], permanent, MAX_ATTEMPTS, delay, error,
        )
        log.warning("send failed", extra={"fields": fields | {"error": error, "now": outcome, "retry_in_s": delay}})


async def run(stop: asyncio.Event) -> None:
    pool = await connect_retrying(lambda: asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4), log)
    wake = asyncio.Event()
    listener = await connect_retrying(lambda: asyncpg.connect(DATABASE_URL), log)
    # NOTIFY outbox_new (sent by the worker after commit) wakes us at once; polling covers a
    # missed notification, e.g. while this connection was reconnecting.
    await listener.add_listener(OUTBOX_CHANNEL, lambda *_: wake.set())
    headers = {}
    if MAIL_TOKEN_FILE:
        with open(MAIL_TOKEN_FILE) as f:
            headers["Authorization"] = f"Bearer {f.read().strip()}"
    log.info("started", extra={"fields": {"endpoint": MAIL_ENDPOINT, "batch": BATCH}})
    async with httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS, headers=headers) as client:
        dispatcher = Dispatcher(pool, client)
        try:
            while not stop.is_set():
                wake.clear()
                try:
                    rows = await pool.fetch(CLAIM_SQL, BATCH, INSTANCE, float(LEASE_SECONDS))
                except (OSError, asyncpg.PostgresError) as exc:
                    log.warning("claim failed", extra={"fields": {"error": repr(exc)}})
                    rows = []
                if rows:
                    results = await asyncio.gather(*(dispatcher.deliver(r) for r in rows), return_exceptions=True)
                    for row, result in zip(rows, results):
                        if isinstance(result, Exception):
                            log.error("deliver failed", extra={"fields": {"outbox_id": str(row["id"]), "error": repr(result)}})
                    if len(rows) == BATCH:
                        continue
                waiters = [asyncio.create_task(stop.wait()), asyncio.create_task(wake.wait())]
                await asyncio.wait(waiters, timeout=POLL_SECONDS, return_when=asyncio.FIRST_COMPLETED)
                for task in waiters:
                    task.cancel()
        finally:
            await listener.close()
            await pool.close()
            log.info("stopped")


def main() -> None:
    stop = asyncio.Event()

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run(stop)

    asyncio.run(runner())


if __name__ == "__main__":
    main()
