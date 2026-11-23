"""store.claim / store.record against a real, migrated PostgreSQL.

Skipped unless TEST_DATABASE_URL is set, e.g. with the Compose stack running:
    TEST_DATABASE_URL=postgresql://edupulse:<password>@localhost:5432/edupulse \
        python -m unittest tests.test_store_integration
Each test creates its own course and deletes it afterwards.
"""
import os
import uuid
from datetime import timedelta
from unittest import IsolatedAsyncioTestCase, skipUnless

from worker import store
from worker.classify import Observation

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@skipUnless(DATABASE_URL, "set TEST_DATABASE_URL to run against PostgreSQL")
class StoreTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
        self.course = f"t-{uuid.uuid4().hex[:8]}"
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

    async def claim(self, resource_id):
        claims = await store.claim(self.pool, "test-worker", 100, 30)
        mine = [c for c in claims if c.resource_id == resource_id]
        self.assertEqual(len(mine), 1)
        # Release anything else we claimed so other tests' rows are not held.
        others = [c.resource_id for c in claims if c.resource_id != resource_id]
        if others:
            await self.pool.execute("UPDATE resources SET lease_token = NULL, lease_until = NULL WHERE id = ANY($1)", others)
        return mine[0]

    async def test_report_during_a_check_still_gets_a_fresh_check(self):
        rid = await self.add_resource("reported")
        item = await self.claim(rid)
        # The student reports while the HTTP check is in flight (after the claim committed).
        await self.pool.execute("INSERT INTO student_reports (resource_id, description) VALUES ($1, 'link is 404')", rid)
        await store.record(self.pool, item, Observation(item.url, status=200, final_url=item.url, latency_ms=5), "ok", None)
        due_in = await self.pool.fetchval("SELECT next_check_at - now() FROM resources WHERE id = $1", rid)
        self.assertLessEqual(due_in, timedelta(seconds=1), "the report's recheck was overwritten by the interval")

    async def test_without_a_report_the_normal_interval_applies(self):
        rid = await self.add_resource("quiet")
        item = await self.claim(rid)
        await store.record(self.pool, item, Observation(item.url, status=200, final_url=item.url, latency_ms=5), "ok", None)
        due_in = await self.pool.fetchval("SELECT next_check_at - now() FROM resources WHERE id = $1", rid)
        self.assertGreater(due_in, timedelta(seconds=590))

    async def test_report_before_the_claim_is_covered_by_this_check(self):
        rid = await self.add_resource("early")
        await self.pool.execute("INSERT INTO student_reports (resource_id, description) VALUES ($1, 'slow')", rid)
        item = await self.claim(rid)
        await store.record(self.pool, item, Observation(item.url, status=200, final_url=item.url, latency_ms=5), "ok", None)
        due_in = await self.pool.fetchval("SELECT next_check_at - now() FROM resources WHERE id = $1", rid)
        self.assertGreater(due_in, timedelta(seconds=590))

    async def test_transport_error_after_auth_required_keeps_the_incident_open(self):
        rid = await self.add_resource("auth", state="AUTH_REQUIRED")
        await self.pool.execute("INSERT INTO incidents (resource_id, state, description) VALUES ($1, 'AUTH_REQUIRED', 'HTTP 401')", rid)
        item = await self.claim(rid)
        recorded = await store.record(self.pool, item, Observation(item.url, error="timeout"), "error", None)
        self.assertEqual((recorded.after.state, recorded.after.incident, recorded.emails_queued), ("SUSPECT", None, 0))
        still_open = await self.pool.fetchval(
            "SELECT count(*) FROM incidents WHERE resource_id = $1 AND resolved_at IS NULL", rid)
        self.assertEqual(still_open, 1)
