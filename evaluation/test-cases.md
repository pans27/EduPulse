# Worker Independent Integration Test Cases

These are completed **manual integration scenarios** with a reproducible method and observable acceptance criteria. They are not new automated unit tests.

| ID | Setup and action | Acceptance evidence |
|---|---|---|
| INT-01 | Configure 20 unique `/load/{i}?delay_ms=500` resources; run 2 workers. | All 20 resources checked; first checking round has exactly one persisted result per resource, workers both contribute. |
| INT-02 | Configure permanent HTTP 500; start monitoring. | Failure counter increments and DOWN incident opens after 3 consecutive failures. Record first failure and incident-open timestamps. |
| INT-03 | Configure `fail_every_k`, `k=3`; observe at least two isolated 503 results followed by 200. | No DOWN incident for this intermittent failure pattern. |
| INT-04 | Start 1 worker with a slow endpoint, observe an active lease, then `docker kill` before a result persists. Wait until lease expiry and launch a new worker instance. | Old lease remains immediately after crash; another worker later saves a result; no result was committed by the killed worker. Log original and replacement worker IDs. |
| INT-05 | Schedule resource `release_at` in future with fake endpoint returning 404, trigger a pre-release check. | Pre-release 404 leaves state unset and consecutive failures 0; next check scheduled for release time. Three post-release 404s create a MISSING incident. |
| INT-06 | Record table counts and one incident identifier/timestamp. Stop workers; recreate local PostgreSQL container without deleting volumes. | Identical table counts and incident details after database returns healthy. |
| PERF-01 | 200 resources, 500 ms simulated delay, common due time, 2 worker replicas. Repeat under a separate course with 4 replicas. | Compare common-due-to-last-result completion time, throughput, mean due-to-result delay, mean HTTP latency, and unique resource counts. |

## SQL evidence

See [`queries.sql`](queries.sql). In `psql`, update the hardcoded course IDs if you use new test dates. The Worker program logs a state change and the resource UUID when it transitions to a new state; correlate these IDs with the database results.

## Operational notes

- Stop workers before changing experimental resource data to avoid contaminating baselines.
- Recreating Postgres with `--force-recreate` preserves the mounted volume; `down -v` may destroy it.
- A SIGTERM is a graceful shutdown; use `docker kill` only in disposable local test environments to simulate an abrupt process crash.
- The `/r/{name}` fake endpoint's 15,000-ms delay led to timeout results in the actual run. Shorter delays (e.g., 1,000 ms) successfully returned HTTP 200.
- Do not claim the old worker's lease was taken over *immediately*: in the recorded experiment a replacement was started after expiry.
