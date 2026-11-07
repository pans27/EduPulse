# EduPulse: Project Proposal

| Name | Student Number | Email |
|---|---|---|
| Carla Pan | 1005884884 | carla.pan@mail.utoronto.ca |
| Byan Zhu | 1013970865 | baiyang.zhu@mail.utoronto.ca |
| Yuan Liu | 1013465386 | vianne.liu@mail.utoronto.ca |

## 1. Motivation

Students rely heavily on online course resources such as course websites, lecture recordings, GitHub repositories, and datasets. These resources are not always reliably accessible. During this term, we found the course website temporarily unavailable. Students may also encounter broken links, inaccessible repositories, or missing lecture recordings. Such problems are usually discovered when students urgently need the materials, such as while preparing for assignments or exams.

The problem affects two groups. Students lose time trying to determine whether a problem is on their end or whether the resource itself is broken. Instructors and teaching assistants often learn about outages only from duplicated emails and forum posts, without knowing whether issues have already been fixed.

The timing of an outage matters. Not all students can choose when to work on coursework; many fit it around classes, jobs, and other commitments. If a required resource is unavailable during that window, they lose an opportunity to make progress and may struggle to find another. Unexpected outages add stress and put some students at a disadvantage. For course staff, relying on student complaints means time is spent triaging messages instead of fixing the underlying issue.

Existing services such as UptimeRobot and Better Stack provide availability checks and notifications, but focus on general websites and APIs rather than courses. Course resources have expected release times and serve users with different needs: staff manage resources and respond to incidents, while students need to know whether materials are accessible and to report problems. EduPulse addresses this gap by organizing resources by course, tracking expected release schedules, and giving staff and students the notifications relevant to each.

## 2. Objectives, Architecture, and Key Features

### 2.1 Objectives

EduPulse monitors the resources a course depends on (course websites, GitHub repositories, datasets, and assignment APIs) and alerts staff and subscribed students when one becomes unavailable or misses its scheduled release. It checks public resources and, where a service's terms permit, endpoints authenticated with staff-provided API tokens.

Core features include a course-based resource registry; periodic HTTP(S) checks; a health state machine (`HEALTHY`, `DEGRADED`, `SUSPECT`, `DOWN`, `AUTH_REQUIRED`, `MISSING`, with `DOWN` only after 3 consecutive failures); scheduled-release tracking; incident history; student reports that trigger an immediate re-check; and a live dashboard with email alerts. Across all API and worker replicas, the system must avoid duplicate checks and lost notifications, and preserve history across redeployments.

### 2.2 System Architecture

```
Browser: dashboard + WebSocket
   │ HTTPS
Nginx
   │ Swarm routing mesh
API ×3: FastAPI, stateless ◀──── NOTIFY ──┐
   │ SQL                                  │
PostgreSQL 18 on DigitalOcean Volume──────┘
   ▲ claim, write                  ▲ claim outbox
Worker ×2–4 (asyncio + httpx)      Dispatcher ×1–2 (worker image)
   │ HTTP(S) checks                │ invoke
Fake site, real resources          DigitalOcean Function ──▶ SendGrid
```

| Component | Technology | Role |
|---|---|---|
| Reverse proxy | Nginx + Certbot | HTTPS, static dashboard, API and WebSocket proxy |
| API ×3 | FastAPI | REST, authentication, WebSocket push; stateless |
| Worker ×2–4 | asyncio + httpx | Claims due resources, runs checks, updates state |
| Dispatcher ×1–2 | Shared Worker image | Delivers outbox rows with retries |
| Database | PostgreSQL 18 | Data, job queue, change events, outbox |
| Notifier | DigitalOcean Functions | Sends alert emails via SendGrid |
| Test target | FastAPI app | Fake course site with switchable failure modes |

**Lifecycle of a check.** (1) Staff register a resource, stored with a check interval and `next_check_at`. (2) Workers poll PostgreSQL for due resources and claim up to 10 with `SELECT … FOR UPDATE SKIP LOCKED`, setting a 30-second lease. (3) Each resource is checked with `HEAD`, falling back to `GET`. (4) One transaction records the result and updates `next_check_at`; if the state changed, it also updates the incident, writes an outbox row when an alert is needed, and issues `NOTIFY`, which PostgreSQL delivers only after commit. (5) Every API replica receives the notification and pushes the change to its WebSocket clients. (6) A dispatcher delivers outbox rows through the DigitalOcean Function and SendGrid.

**Main tables:** `users`, `courses`, `course_members`, `resources` (including optional release times), `credentials`, `check_results`, `incidents`, `student_reports`, `subscriptions`, `notification_outbox`, managed with Alembic migrations.

### 2.3 Required Course Technologies

**Docker and Docker Compose.** The API, worker, fake course site, and Nginx each have a Dockerfile; the dispatcher reuses the worker image, and PostgreSQL uses the official `postgres:18` image. `docker compose up` runs the full stack locally.

**PostgreSQL and persistent storage.** PostgreSQL provides the job queue (`SKIP LOCKED`), cross-replica events (`LISTEN/NOTIFY`), and transactional consistency for state, incidents, and the outbox. Its data directory (`/var/lib/postgresql` in the version 18 image) lives on a DigitalOcean Volume. A Volume can attach to only one Droplet, so Postgres is pinned to that node with a placement constraint (`node.labels.db == true`). Raw check results are kept for 14 days, then rolled up hourly.

**Deployment provider: DigitalOcean.** Our users are in one region, so Fly.io's edge distribution adds little, and its built-in scaling would not satisfy the orchestration requirement. DigitalOcean provides Droplets, Volumes, monitoring, and Functions for about $25 per month.

**Orchestration: Docker Swarm.** The cluster has three Droplets in one VPC: a 2 GB manager running Postgres and Nginx, and two 1 GB nodes running 3 API replicas, 2–4 workers, and the dispatcher. Swarm reuses our Compose files and is simpler to operate than Kubernetes for three people in five weeks; the cost is manual scaling based on queue lag.

**Monitoring and observability.** DigitalOcean alerts on CPU, memory, and Volume disk usage, and an Uptime Check monitors EduPulse's own `/health` endpoint, including an SSL-expiry alert. The application reports checks per minute, failure rate, open incidents, and queue lag, and emits structured JSON logs.

### 2.4 Advanced Features

**Real-time updates.** Workers broadcast resource IDs through PostgreSQL `LISTEN/NOTIFY`. API replicas fetch updated states and push them to clients via WebSockets. This avoids the 8,000-byte payload limit and lets disconnected clients recover missed updates from the database.

**Serverless email notifications (external service).** Alerts go through a transactional outbox to a DigitalOcean Function that sends email via SendGrid (step 6). The function keeps the SendGrid key out of the cluster and runs only when an alert occurs. Because SendGrid does not deduplicate retries, the dispatcher marks rows as sent and retries failures with backoff, giving at-least-once delivery deduplicated by outbox ID.

**Security enhancements.** Passwords are hashed with Argon2, and course roles let staff manage resources while students view, subscribe, and report. Staff API tokens are encrypted in PostgreSQL and never returned by the API. The encryption key and the database credentials are stored as Docker secrets, and Nginx serves HTTPS with Let's Encrypt certificates.

### 2.5 Design Decisions and Trade-offs

| Decision | Alternative | Rationale | Risk |
|---|---|---|---|
| Postgres as job queue and `LISTEN/NOTIFY` fan-out | Redis or RabbitMQ | Small workload; already durable; fewer services | Throughput ceiling at large scale; listeners reconnect after a database restart |
| Single Postgres instance | Streaming replication | Simple and consistent; failover could not be validated in five weeks | Single point of failure; Volume persistence and snapshots |
| 3 consecutive failures before `DOWN` | Alert on first failure | Avoids alerts on transient errors | Slower detection (about 3 minutes); N is tunable |
| Outbox + Function for email | Worker calls SendGrid directly | No alert lost between commit and send; email key isolated | Extra hop and cold starts; retries with backoff |

### 2.6 Scope and Feasibility

The scope is 2–3 sample courses, 15–30 resources, and a fake course site. We will not integrate with Quercus, because Canvas requires multi-user applications to obtain tokens through OAuth with institution-issued developer keys. We will not automate logins or crawl websites, and checks are rate-limited per host. CI/CD with GitHub Actions is a stretch goal.

### 2.7 Evaluation Plan

All tests use the fake course site.

| Metric | Target | Method |
|---|---|---|
| Detection latency | Persistent failure confirmed within about 3 minutes | Inject 404s; compare with incident creation time |
| False alarms | No `DOWN` for isolated transient failures | Inject failures between successes; inspect state transitions |
| Worker coordination | No resource leased by two workers at once under valid leases | Run two workers concurrently; inspect claims |
| Worker recovery | Unfinished tasks reassigned after lease expiry | Stop a worker mid-check; verify another retries it |
| Persistence | History survives redeployment | Redeploy services; compare database records |
| Scalability | Queue lag falls as workers increase | Add simulated latency; compare 2 and 4 workers |

Evidence will come from PostgreSQL queries, application logs, and DigitalOcean metrics.

## 3. Project Plan and Team Responsibilities

### 3.1 Responsibilities

| Member | Primary ownership | Evaluation and documentation |
|---|---|---|
| Carla Pan: API and user-facing | API endpoints, authentication and roles, WebSocket updates, dashboard, metrics | User guide, video demo |
| Byan Zhu: Check engine and data | Schema and migrations, worker leasing, check classification, state machine, incidents, scheduled releases, outbox writes | Evaluation analysis, `ai-session.md`, slides |
| Yuan Liu: Infrastructure | Docker and Compose, fake site, DigitalOcean, Swarm and secrets, Nginx, monitoring, dispatcher and Function, CI/CD | Load and failover experiments, deployment guide |

Workloads are about 65–70 hours each, peaking at different times.

### 3.2 Development Stages

| Period | Milestone (owner) |
|---|---|
| Oct 31 – Nov 7 | Repository, Compose skeleton, fake site (Yuan); schema and migrations (Byan); proposal |
| Nov 8 – Nov 14 | Local MVP: API and dashboard (Carla), worker and state machine (Byan); skeleton Swarm deployment (Yuan) |
| Nov 15 – Nov 21 | Full Swarm deployment with Volume, HTTPS, and monitoring (Yuan); real-time updates (Carla) |
| Nov 22 – Nov 24 | Demo hardening; introduction (Nov 23); slides frozen (Nov 24) |
| Nov 25 – Dec 5 | Notifications (Byan, Yuan), authentication (Carla), evaluation (Byan, Yuan), CI/CD (Yuan), README and video (all) |

### 3.3 Integration Strategy

In week 1 we agree on the schema, owned by Byan, and the API contracts. Carla builds against them, and Yuan keeps the Compose stack runnable on the main branch. We integrate incrementally: a shared database first, then end-to-end state updates and notifications, then Swarm deployment. Integration tests cover registration, checks, incidents, dashboard updates, and persistence.

### 3.4 Dependencies and Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Cloud setup delays deployment | High | Develop on Compose; skeleton deployment by Nov 14 |
| Schema changes break others' work | High | Schema fixed in week 1; changes only through reviewed migrations |
| Data loss from Volume misconfiguration | High | Rehearse a Postgres redeploy; snapshot before infrastructure changes |
| Leasing or `LISTEN/NOTIFY` harder than expected | Medium | Prototype in week 2; fallback to one polling worker |

## 4. Initial Team Reasoning and AI-Assisted Exploration

### 4.1 Initial Team Reasoning

We were inspired by the sample Event Logging and Analytics System. A course website was occasionally unavailable when we needed lecture notes or handouts, so we wanted a system that alerts staff before students report problems. We first considered checking only whether a site was online, then expanded to individual resources, such as assignment links and lecture recordings, to differ from general uptime tools.

Our initial design had a backend API, one monitoring worker, and PostgreSQL storing resources, check history, and incidents, with Healthy, Unhealthy, or Unknown labels and a predefined manifest for detecting missing materials. Open questions were how to avoid false alarms without delaying alerts, how often to check, how to handle login-protected resources, and whether one worker could keep up or would lose tasks if it crashed.

### 4.2 Exploration and Evaluation

**Private resources.** Most course materials are private, and Assignment 3 had us authenticate to Fly.io with API tokens, so we thought of checking Quercus through Canvas's REST API with staff tokens. An AI-assisted search led us to Canvas's OAuth documentation, which prohibits multi-user applications from collecting manually generated tokens. We therefore limited token-based checks to services that allow them, such as GitHub.

**Event broadcasting.** We first considered RabbitMQ or Redis, the options we knew. AI suggested PostgreSQL `LISTEN/NOTIFY` instead; with research, we determined it satisfies our use case despite having limits, shaping the design in Section 2.4.

### 4.3 How the Final Proposal Differs from Initial Thinking

The final design replaces the single worker with lease-based workers, the three health labels with a six-state machine that alerts only after repeated failures, and the static manifest with scheduled release times, and adds real-time updates.
