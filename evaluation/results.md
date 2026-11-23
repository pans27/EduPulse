# Worker Integration & Performance Evaluation — Local Results

**Test date:** 2026-11-23  
**Environment:** Windows, Docker Desktop / Docker Compose, PostgreSQL, fake HTTP site  
**Scope:** Independent integration verification and performance evaluation of the merged Worker implementation. 

## Summary

| Test | Observed result | Interpretation |
|---|---|---|
| Worker coordination | Two workers each processed 10 of 20 due resources in the first round; 20 result rows, 20 distinct resources | No duplicate **persisted check results** observed in that round; not a formal proof of all lease interleavings |
| Persistent failure detection | Three HTTP 500 failures; initial failure `05:17:36.410097` UTC, DOWN confirmed `05:18:07.999451` UTC | **31.59 s** from first observed failure to confirmed DOWN; not fault-injection-to-detection latency |
| False-alarm avoidance | Intermittent sequence included 200, 200, 503, 200, 200, 503, 200, 200; zero DOWN incidents for that resource | Isolated transient failures did not trigger a confirmed incident in this scenario |
| Crash recovery | Original worker killed while holding lease, with 0 persisted check results; lease later expired; replacement worker processed resource | Expired work was reclaimable; task processing resumed with no lost resource |
| Scheduled release | Pre-release 404 ignored; post-release 404s at `05:40:15`, `05:40:31`, `05:40:47` UTC led to MISSING | No premature incident; MISSING incident created ~**32.35 s** after scheduled release |
| Persistence | Recreated PostgreSQL container; check_results `203→203`, incidents `3→3`, resources `25→25` | Counts and the specific MISSING incident remained available with the existing persistent volume |
| Scalability | 2 workers: 8.79 s, 22.74 checks/s; 4 workers: 6.34 s, 31.54 checks/s | **38.7%** higher throughput; ~**1.39×** observed speedup, single runs |

## 1. Worker coordination

An evaluation course with 20 unique resources used `/load/{i}?delay_ms=500` and two worker replicas. In the first completed checking round, the workers handled 10 resources each. The database contained one persisted result per resource, without duplicates in that round.

**Limitation:** Result uniqueness does not by itself prove that a resource can never be claimed concurrently. A stronger claim requires observing lease-owner histories or running dedicated concurrent-claim tests (the team's unit/integration test suite may provide additional evidence).

## 2. Persistent failure and false alarms

A permanent HTTP 500 fault produced repeated failures. A DOWN incident opened at `2026-11-23 05:18:07.999451+00`, compared with first observed failure at `05:17:36.410097+00`, a difference of ~31.59 seconds. For a separate endpoint configured to return HTTP 503 on every third request, two non-consecutive 503s were followed by successful 200 rechecks, with **zero DOWN incidents**.

**Limitation:** The permanent error was configured before monitoring started. The reported 31.59 seconds measures **first observed failure → DOWN**, not **failure injection → DOWN**. Zero false incidents refers only to the observed deterministic transient-fault sequence.

## 3. Crash recovery and state transitions

- Worker A identifier: `5295513d6607:1`; forcibly terminated with `docker kill` while holding a lease.
- The resource had a retained lease owner/token (`301fd67a-fc4f-4225-9327-3f677a8b3cdd`), expiry `05:27:11.354063+00`, and **0** persisted check results immediately after the crash.
- The database later confirmed that the lease was expired (`lease_expired = true`).
- Worker B identifier: `7424fbcc41eb:1`; started after expiry. It reclaimed and checked the resource.
- The fake endpoint's initial 15-second delay exceeded the effective HTTP request timeout, yielding three `timeout` results (`05:33:42`, `05:34:08`, `05:34:34` UTC). DOWN incident opened on the third failure.
- After changing the endpoint delay to 1,000 ms and requesting a new check, the worker recorded HTTP 200 at `05:35:45.828787+00`, state `HEALTHY`, `consecutive_failures=0`, and resolved the incident. Incident opened at `05:34:34.476765+00`, lasting ~71.35 seconds.

**Limitations:** The replacement worker was started several minutes *after* the lease expired. This proves that expired work can be reclaimed, **not** the elapsed time of automatic failover with a continuously available standby worker. The subsequent timeout results are valid persisted checks and not duplicate results from the original killed worker.

## 4. Scheduled releases

Test resource release time: `2026-11-23 05:40:15.015055+00`.

| Check time (UTC) | HTTP status | Interpretation |
|---|---:|---|
| 05:39:02.883339 | 404 | Before release: state remained NULL, failure count zero, next check rescheduled to release time |
| 05:40:15.149254 | 404 | First post-release failure |
| 05:40:31.272870 | 404 | Second post-release failure |
| 05:40:47.367013 | 404 | Third post-release failure; MISSING incident opened |

Subsequent checks continued to return 404; the MISSING incident remained open at the time of verification. Time from scheduled release to opening: ~32.35 seconds.

## 5. Persistence

After stopping the Worker and issuing `docker compose up -d --no-deps --force-recreate postgres`, PostgreSQL became healthy and the following records remained:

| Table | Before | After |
|---|---:|---:|
| check_results | 203 | 203 |
| incidents | 3 | 3 |
| resources | 25 | 25 |

The particular MISSING incident retained `opened_at=2026-11-23 05:40:47.367013+00` and `resolved_at=NULL`.

**Limitation:** This tests container recreation with the existing volume, not destruction of the volume or recovery from backup.

## 6. Scalability (200 resources, simulated 500 ms HTTP latency)

Two distinct, otherwise equivalent experiment courses were used. Each contained 200 unique load-test URLs, with a common `next_check_at` timestamp and a 3,600-second repeat interval. Two and four worker replicas used the same defaults (`WORKER_BATCH=10`, 30-second leases). All 200 resources were checked exactly once in each course.

| Metric | 2 workers | 4 workers | Change |
|---|---:|---:|---:|
| Resources | 200 | 200 | Same |
| Persisted results | 200 | 200 | Same |
| Completion time from scheduled due time | 8.79 s | 6.34 s | **27.9% less** |
| Throughput (`200/completion_time`) | 22.74 checks/s | 31.54 checks/s | **38.7% higher** |
| Mean due-to-result delay | 5,139.00 ms | 4,614.91 ms | **10.2% less** |
| Mean HTTP latency | 605.62 ms | 570.81 ms | **5.7% less** |
| Duplicate persisted results | 0 | 0 | No observed duplicates |

**Speedup:** `8.79 / 6.34 ≈ 1.39×`.  
**Throughput improvement:** `(31.54 / 22.74 − 1) × 100 ≈ 38.7%`.

**Measurement definitions and caveats:**

- *Completion time* = final persisted result timestamp minus the common scheduled due time. This is not measured from the instant Workers were started.
- *Throughput* = completed resources / completion time, not the instantaneous HTTP processing rate.
- *Mean due-to-result delay* includes queueing, check execution, and result persistence; it is **not pure queue lag**.
- Only one local run was performed per configuration. The experiments used different course IDs and ran alongside other possible due resources. Container startup jitter, PostgreSQL contention, fake-server concurrency, and scheduling overhead were not independently measured. The results show observed scaling improvement, not a guaranteed production speedup.

### Report-ready English paragraph

> We evaluated horizontal scalability using 200 simulated HTTP resources with a configured 500 ms response delay. With the same batch size and lease configuration, increasing the number of worker replicas from two to four reduced the time from the common scheduled due timestamp to the last persisted check result from 8.79 s to 6.34 s (27.9%) and increased effective throughput from 22.74 to 31.54 checks/s (38.7%). Mean due-to-result delay decreased from 5,139 ms to 4,615 ms. Both configurations produced exactly 200 results for 200 unique resources. These observations support the benefit of scaling out the workers, although the speedup was sublinear (~1.39×) and the measurements were single local runs. The reported delay combines scheduling, HTTP execution, and persistence rather than isolating queue lag.

## General limitations / follow-up

The tests use a fake site and local Docker Compose, not a controlled production load test. If schedule permits, repeat scalability runs several times and report variance. A direct queue-lag measurement would require recording claim/start times rather than inferring them from result timestamps. Avoid interpreting absence of duplicate check rows in one workload as a formal concurrency guarantee.
