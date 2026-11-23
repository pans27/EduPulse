"""store.claim / store.record against a real, migrated PostgreSQL.

Run only against a disposable database: claim() takes any due resource, exactly like a real
worker, so pointing these tests at a database with live resources would steal their checks. The
tests refuse to run if the database holds resources other than their own.

Skipped unless TEST_DATABASE_URL is set. A throwaway database, from the repository root:
    docker compose -p eptest up -d --wait postgres && docker compose -p eptest run --rm --build migrate
    # then run the tests in a container on network eptest_default with
    #   TEST_DATABASE_URL=postgresql://edupulse:<password>@postgres:5432/edupulse
    docker compose -p eptest down -v
"""
import asyncio
import os
import uuid
from datetime import timedelta
from unittest import IsolatedAsyncioTestCase, skipUnless

from worker import store
from worker.classify import Observation

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
PREFIX = "t-"

# The statements the API runs for a student report (PostgresRegistry.report), in one transaction.
REPORT_UPDATE = (
    "UPDATE resources SET next_check_at = least(next_check_at, now()), recheck_requested = true WHERE id = $1"
)
REPORT_INSERT = "INSERT INTO student_reports (resource_id, description) VALUES ($1, 'link is 404')"


def ok(item):
    return Observation(item.url, status=200, final_url=item.url, latency_ms=5)


@skipUnless(DATABASE_URL, "set TEST_DATABASE_URL to a disposable PostgreSQL database")
class StoreTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg
        self.asyncpg = asyncpg
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
        foreign = await self.pool.fetchval("SELECT count(*) FROM resources WHERE course_id NOT LIKE $1", PREFIX + "%")
        if foreign:
            await self.pool.close()
            self.fail(f"TEST_DATABASE_URL has {foreign} resources that are not test fixtures; use a disposable database")
        self.course = f"{PREFIX}{uuid.uuid4().hex[:8]}"
        await self.pool.execute("INSERT INTO courses (id, name) VALUES ($1, 'test')", self.course)

    async def asyncTearDown(self):
        await self.pool.execute("DELETE FROM resources WHERE course_id = $1", self.course)
        await self.pool.execute("DELETE FROM courses WHERE id = $1", self.course)
        await self.pool.close()

    async def add_resource(self, name, state=None, failures=0, interval=600):
        return await self.pool.fetchval(
            "INSERT INTO resources (course_id, name, url, check_interval_seconds, next_check_at, state, consecutive_failures) "
            "VALUES ($1, $2, $3, $4, now() - interval '1 second', $5, $6) RETURNING id",
            self.course, name, f"http://site.test/{name}", interval, state, failures,
        )

    async def claim_only(self, resource_id):
        claims = await store.claim(self.pool, "test-worker", 10, 30)
        self.assertEqual([c.resource_id for c in claims], [resource_id])  # nothing else is due here
        return claims[0]

    async def due_in(self, resource_id):
        return await self.pool.fetchval("SELECT next_check_at - now() FROM resources WHERE id = $1", resource_id)

    async def report(self, resource_id):
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(REPORT_UPDATE, resource_id)
            await conn.execute(REPORT_INSERT, resource_id)

    async def test_report_during_a_check_still_gets_a_fresh_check(self):
        rid = await self.add_resource("reported")
        item = await self.claim_only(rid)
        await self.report(rid)  # committed while the HTTP check is in flight
        await store.record(self.pool, item, ok(item), "ok", None)
        self.assertLessEqual(await self.due_in(rid), timedelta(seconds=1), "the report's recheck was lost")

    async def test_report_committing_while_the_worker_waits_for_the_lock(self):
        # The report's transaction holds the row lock; the worker's record() blocks on it and
        # resumes only after the report commits. The flag must survive that overlap.
        rid = await self.add_resource("overlap")
        item = await self.claim_only(rid)
        conn = await self.asyncpg.connect(DATABASE_URL)
        try:
            tx = conn.transaction()
            await tx.start()
            await conn.execute(REPORT_UPDATE, rid)
            await conn.execute(REPORT_INSERT, rid)
            recording = asyncio.create_task(store.record(self.pool, item, ok(item), "ok", None))
            await asyncio.sleep(0.5)
            self.assertFalse(recording.done(), "record() should be waiting for the report's lock")
            await tx.commit()
            await recording
        finally:
            await conn.close()
        self.assertLessEqual(await self.due_in(rid), timedelta(seconds=1), "the report's recheck was lost")

    async def test_without_a_report_the_normal_interval_applies(self):
        rid = await self.add_resource("quiet")
        item = await self.claim_only(rid)
        await store.record(self.pool, item, ok(item), "ok", None)
        self.assertGreater(await self.due_in(rid), timedelta(seconds=590))

    async def test_report_before_the_claim_is_covered_by_this_check(self):
        rid = await self.add_resource("early")
        await self.report(rid)
        item = await self.claim_only(rid)  # the claim consumes the request
        await store.record(self.pool, item, ok(item), "ok", None)
        self.assertGreater(await self.due_in(rid), timedelta(seconds=590))
        self.assertFalse(await self.pool.fetchval("SELECT recheck_requested FROM resources WHERE id = $1", rid))

    async def test_transport_error_after_auth_required_keeps_the_incident_open(self):
        rid = await self.add_resource("auth", state="AUTH_REQUIRED")
        await self.pool.execute("INSERT INTO incidents (resource_id, state, description) VALUES ($1, 'AUTH_REQUIRED', 'HTTP 401')", rid)
        item = await self.claim_only(rid)
        recorded = await store.record(self.pool, item, Observation(item.url, error="timeout"), "error", None)
        self.assertEqual((recorded.after.state, recorded.after.incident, recorded.emails_queued), ("SUSPECT", None, 0))
        still_open = await self.pool.fetchval(
            "SELECT count(*) FROM incidents WHERE resource_id = $1 AND resolved_at IS NULL", rid)
        self.assertEqual(still_open, 1)


@skipUnless(DATABASE_URL, "set TEST_DATABASE_URL to a disposable PostgreSQL database")
class GuardTests(IsolatedAsyncioTestCase):
    async def test_refuses_a_database_with_other_resources(self):
        import asyncpg
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await pool.execute("INSERT INTO courses (id, name) VALUES ('live-course', 'live') ON CONFLICT DO NOTHING")
            rid = await pool.fetchval(
                "INSERT INTO resources (course_id, name, url, next_check_at) VALUES ('live-course', 'x', 'http://x', now() + interval '1 hour') RETURNING id")
            probe = StoreTests("test_without_a_report_the_normal_interval_applies")
            with self.assertRaises(AssertionError):
                await probe.asyncSetUp()
        finally:
            await pool.execute("DELETE FROM resources WHERE id = $1", rid)
            await pool.execute("DELETE FROM courses WHERE id = 'live-course'")
            await pool.close()
