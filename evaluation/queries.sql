-- EduPulse historical evaluation evidence (read-only).
-- Run from repo root:
--   Get-Content evaluation/queries.sql | docker compose exec -T postgres psql -U edupulse -d edupulse
-- Course IDs correspond to the original 2026-11-23 local experiments.

-- Worker coordination: total and uniqueness of recorded checks.
SELECT COUNT(*) AS result_rows,
       COUNT(DISTINCT cr.resource_id) AS distinct_resources
FROM check_results cr JOIN resources r ON r.id = cr.resource_id
WHERE r.course_id = 'eval-coord-20261123';

-- First check time for each coordination resource (avoid counting later rounds).
SELECT r.id, r.name, MIN(cr.checked_at) AS first_check
FROM resources r JOIN check_results cr ON cr.resource_id = r.id
WHERE r.course_id = 'eval-coord-20261123'
GROUP BY r.id, r.name ORDER BY r.name;

-- Persistent vs intermittent failures: raw observations.
SELECT r.name, cr.checked_at, cr.http_status, cr.outcome, cr.error_message
FROM check_results cr JOIN resources r ON r.id = cr.resource_id
WHERE r.course_id = 'eval-fault-20261123'
ORDER BY r.name, cr.checked_at;

-- Incidents in fault/recovery/release courses.
SELECT r.course_id, r.name, i.state AS incident_type, i.opened_at, i.resolved_at
FROM incidents i JOIN resources r ON r.id = i.resource_id
WHERE r.course_id IN ('eval-fault-20261123',
                      'eval-recovery-20261123',
                      'eval-release-20261123')
ORDER BY i.opened_at;

-- Scheduled release: distinguish pre- and post-release checks.
SELECT r.name, r.release_at, cr.checked_at, cr.http_status, cr.outcome,
       cr.checked_at < r.release_at AS before_release
FROM resources r JOIN check_results cr ON cr.resource_id = r.id
WHERE r.course_id = 'eval-release-20261123'
ORDER BY cr.checked_at;

-- Preserved data: snapshot counts. These will naturally change as workers run.
SELECT (SELECT COUNT(*) FROM check_results) AS checks,
       (SELECT COUNT(*) FROM incidents) AS incidents,
       (SELECT COUNT(*) FROM resources) AS resources;

-- Historical benchmark cohorts: total checks and uniqueness.
SELECT r.course_id, COUNT(cr.id) AS total_checks,
       COUNT(DISTINCT cr.resource_id) AS distinct_resources,
       ROUND(AVG(cr.latency_ms)::numeric, 2) AS avg_http_latency_ms,
       MIN(cr.checked_at) AS first_result,
       MAX(cr.checked_at) AS last_result
FROM resources r LEFT JOIN check_results cr ON cr.resource_id = r.id
WHERE r.course_id IN ('eval-scale2-20261123', 'eval-scale4-20261123')
GROUP BY r.course_id ORDER BY r.course_id;

-- For exact benchmark throughput and completion time, preserve each cohort's
-- ORIGINAL common due timestamp ($due2 / $due4 in PowerShell). After checks,
-- resources.next_check_at typically changes, so the present value is NOT
-- the original scheduled due time. Original aggregate values are in results.md.
