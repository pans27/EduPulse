"""PostgreSQL side of the worker: claim due resources with a lease, then record each result.

Claim and record are separate short transactions so no row lock is held during the HTTP
check. The lease_token fences a slow worker: if its lease expired and another worker claimed
the resource, the stale result is discarded instead of overwriting the newer one.
"""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import asyncpg

from . import outbox
from .classify import Observation, Outcome
from .states import Resource, Transition, transition

CLAIM_SQL = """
WITH due AS (
    SELECT id FROM resources
    WHERE next_check_at <= now() AND (lease_until IS NULL OR lease_until < now())
    ORDER BY next_check_at
    LIMIT $1
    FOR UPDATE SKIP LOCKED
)
UPDATE resources AS r
SET lease_owner = $2, lease_until = now() + make_interval(secs => $3), lease_token = gen_random_uuid(),
    recheck_requested = false  -- this check covers every report made before it started
FROM due
WHERE r.id = due.id
RETURNING r.id, r.url, r.lease_token
"""

# Locks the row (only while we still hold the lease). recheck_requested is a column of the locked
# row, so if a report's transaction held the lock, PostgreSQL re-reads the committed value after
# the wait. The open incident is read in this same transaction.
LOCK_SQL = """
SELECT r.state, r.consecutive_failures, r.check_interval_seconds, r.release_at, r.course_id, r.name,
       r.recheck_requested, now() AS now,
       (SELECT i.state FROM incidents i WHERE i.resource_id = r.id AND i.resolved_at IS NULL) AS open_incident
FROM resources r
WHERE r.id = $1 AND r.lease_token = $2
FOR UPDATE OF r
"""


@dataclass(frozen=True)
class Claim:
    resource_id: UUID
    url: str
    lease_token: UUID


@dataclass(frozen=True)
class Recorded:
    before: str | None
    after: Transition
    checked_at: datetime
    emails_queued: int = 0


async def claim(pool: asyncpg.Pool, worker_id: str, batch: int, lease_seconds: int) -> list[Claim]:
    rows = await pool.fetch(CLAIM_SQL, batch, worker_id, float(lease_seconds))
    return [Claim(row["id"], row["url"], row["lease_token"]) for row in rows]


def _describe(observation: Observation) -> str:
    if observation.error:
        return observation.error
    return f"HTTP {observation.status}" + (f" via {observation.final_url}" if observation.final_url != observation.requested_url else "")


async def record(
    pool: asyncpg.Pool, item: Claim, observation: Observation, outcome: Outcome, notify_channel: str | None
) -> Recorded | None:
    """Apply one check in a single transaction. Returns None if the lease was lost."""
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(LOCK_SQL, item.resource_id, item.lease_token)
        if row is None:
            return None  # lease expired and another worker took over; its result wins
        before = row["state"]
        step = transition(
            Resource(before, row["consecutive_failures"], row["check_interval_seconds"], row["release_at"],
                     row["open_incident"]),
            outcome,
            row["now"],
        )
        next_check_at = step.next_check_at
        if row["recheck_requested"]:
            # A student reported the resource while this check ran. The report could not move
            # next_check_at (the row was already due), so honour it here instead of overwriting it
            # with the normal interval.
            next_check_at = min(next_check_at, row["now"])
        detail = _describe(observation)
        await conn.execute(
            "INSERT INTO check_results (resource_id, http_status, latency_ms, outcome, error_message) "
            "VALUES ($1, $2, $3, $4, $5)",
            item.resource_id, observation.status, observation.latency_ms, outcome,
            observation.error,
        )
        await conn.execute(
            "UPDATE resources SET state = $2, consecutive_failures = $3, next_check_at = $4, "
            "recheck_requested = false, lease_owner = NULL, lease_until = NULL, lease_token = NULL WHERE id = $1",
            item.resource_id, step.state, step.consecutive_failures, next_check_at,
        )
        incident_id = None
        if step.incident == "open":
            incident_id = await conn.fetchval(
                "INSERT INTO incidents (resource_id, state, description) VALUES ($1, $2, $3) "
                "ON CONFLICT (resource_id) WHERE resolved_at IS NULL DO NOTHING RETURNING id",
                item.resource_id, step.state, detail,
            )
        elif step.incident == "update":
            incident_id = await conn.fetchval(
                "UPDATE incidents SET state = $2, description = $3 "
                "WHERE resource_id = $1 AND resolved_at IS NULL RETURNING id",
                item.resource_id, step.state, detail,
            )
        elif step.incident == "resolve":
            incident_id = await conn.fetchval(
                "UPDATE incidents SET resolved_at = now() WHERE resource_id = $1 AND resolved_at IS NULL RETURNING id",
                item.resource_id,
            )
        emails = 0
        kind = outbox.kind_for(step.incident)
        if kind is not None:
            # Same transaction as the state change: the alert exists if and only if it committed.
            message = outbox.payload(
                course_id=row["course_id"], resource_name=row["name"], url=item.url,
                before=before, after=step.state, detail=detail, at=row["now"],
            )
            status = await conn.execute(outbox.INSERT_SQL, item.resource_id, incident_id, kind, message, row["course_id"])
            emails = int(status.split()[-1])  # "INSERT 0 <rows>"
            if emails:
                await conn.execute("SELECT pg_notify($1, '')", outbox.OUTBOX_CHANNEL)
        if step.changed(before) and notify_channel:
            # Delivered only after COMMIT, so API replicas never push an uncommitted state.
            await conn.execute("SELECT pg_notify($1, $2)", notify_channel, str(item.resource_id))
        return Recorded(before, step, row["now"], emails)
