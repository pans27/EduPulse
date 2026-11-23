# EduPulse — Worker Integration & Performance Evaluation

This directory documents local PostgreSQL/Docker Compose experiments performed on **2026-11-23**. It does **not** replace the Worker implementation.

## Contents

- [`results.md`](results.md): observed results, methodology, limitations, and report-ready analysis.
- [`test-cases.md`](test-cases.md): independent integration-validation cases and how to repeat them.
- [`queries.sql`](queries.sql): read-only SQL queries for examining persisted experimental evidence.
- [`scalability.ps1`](scalability.ps1): optional, repeatable 2-versus-4-worker benchmark runner with a *fresh* course for each run.

## Scope and assumptions

- Run from the **repository root** on Windows PowerShell with Docker Desktop and the current merged Worker code.
- Docker Compose services are named `postgres`, `worker`, and `fake-site`.
- The database and user are `edupulse`; the fake site supports `/load/{i}?delay_ms=500`.
- The latest Worker uses `WORKER_BATCH=10`, `WORKER_LEASE_SECONDS=30`, and `WORKER_POLL_SECONDS=2` as defaults, with concurrent checks within a batch.
- Existing local PostgreSQL experiment data are not exported into this directory. Only their observed aggregate results and command output summaries are preserved.
- The attached historical EduPulse ZIP includes an older Worker placeholder; confirm all assumptions against the **current checkout** before rerunning anything.

## Before opening a PR

1. Copy this `evaluation/` directory into the current repository. Check `git status` and ensure no secrets or local `.env` files are staged.
2. Read [`results.md`](results.md) and verify the historical figures against your local database if it is still available.
3. Review scripts; benchmark runs will add courses/resources and start/stop local Worker containers. They do not modify core Worker code.
4. Optionally rerun the two benchmarks on a clean or isolated local test database to assess repeatability.
5. Commit the documentation and scripts on `feat/worker-evaluation`, then open a PR.

**Important:** `scalability.ps1` stops the Compose `worker` service before and after an experiment. Do not run it while collaborators rely on that local stack, and never use it on a production deployment.
