# EduPulse presentation and live demo

6 minutes, strictly enforced, then about 2 minutes of Q&A. The plan follows the presentation rubric:
**Clarity & Communication (6)**, **Technical Progress & Demonstration (10)**: containers,
persistence, orchestration, monitoring; and **Advanced Features & Completion Plan (4)**. Most of the
time goes to evidence for those, not to a product walkthrough.

## Roles and setup

- **Speaker:** explains, from slides with keywords only, no script.
- **Operator:** Yuan, on Yuan's laptop (the only one with SSH access to the Droplets); runs every
  demo command and switches windows.
- **One laptop** for everything, display **mirrored** (not extended). Arrive at 12:05 to test the
  projector (USB-C or HDMI).
- Everyone answers Q&A; see the question list at the end.

### Windows, open before our turn

| # | Window | Content |
|---|---|---|
| 1 | Slides | |
| 2 | Browser A (normal) | https://138-197-173-188.sslip.io signed in as **staff** (`vianne.liu@mail.utoronto.ca`), course **ECE1779** |
| 3 | Browser B (private window) | Same site, signed in as **student** (`yuanliu0922@gmail.com`), course **ECE1779** |
| 4 | Terminal, large font | `scripts/demo.sh watch` (states, open incidents, email outbox every 2 s) |
| 5 | Terminal, large font | Commands below, in the repository root |
| 6 | Browser tab | DigitalOcean console → Monitoring (Uptime check, Alerts), already signed in |
| 7 | Backup | Short recordings of each live step, and screenshots, in case the cloud or Wi-Fi fails |

Terminal font: large enough for the back row (about 20 pt).

### 30 minutes before

```bash
cd ~/ECE1779/EduPulse && git switch main && git pull
```

```bash
scripts/demo.sh services
```

All services full, our images on one commit.

```bash
scripts/demo.sh remove a1-handout
```

```bash
scripts/demo.sh fix a1-handout && scripts/demo.sh mail-reset
```

**Register the demo resource in advance** (saves 30 s on stage). In Browser A, **Register a
resource**: Name `Assignment 1 handout`, Resource URL `http://fake-site:8000/r/a1-handout`, Check
interval `10`, Expected release empty → **Register**. Wait until it is HEALTHY in both browsers and
"Live updates connected" shows under the heading.

## The 6 minutes

| Time | Section | Rubric |
|---|---|---|
| 0:00–0:40 | Problem and users | Clarity |
| 0:40–1:30 | Architecture | Clarity, Technical |
| 1:30–2:15 | **Live 1:** outage detected, pushed live, emailed | Advanced features 1 and 2 |
| 2:15–2:55 | **Live 2:** orchestration | Orchestration |
| 2:55–3:40 | **Live 3:** persistence | Persistence |
| 3:40–4:10 | **Live 4:** monitoring and logs | Monitoring |
| 4:10–5:20 | Advanced features and completion plan | Advanced features |
| 5:20–6:00 | Failover evidence, limitations, what's left | Technical, plan |

At the 1-minute hand signal you should be starting the advanced-features slide. If a live step
fails, switch to its recording at once; troubleshooting time counts toward the 6 minutes.

### 0:00–0:40 Problem and users (slide 1)

Keywords: course sites, recordings and assignment links break or miss their release; students find
out first. EduPulse checks them, shows status live, emails staff and students. Users: course staff
register resources; students view and report.

### 0:40–1:30 Architecture (slide 2: one diagram)

Point at each part once:
- 3 DigitalOcean Droplets in a Docker Swarm, private network.
- Nginx with HTTPS → **API ×3** (stateless) → **PostgreSQL**, the only place state lives:
  resources, check history, incidents, users, sessions, email outbox.
- **Workers ×2** claim due checks from Postgres with leases, so two workers never check the same
  resource and a dead worker's job is retried.
- **Dispatcher** sends alert emails from the outbox.
- Postgres data on a **DigitalOcean Volume**; monitoring by DigitalOcean metrics, alerts and an
  uptime check.

### 1:30–2:15 Live 1: an outage, detected and pushed (windows 2–5)

Operator:

```bash
scripts/demo.sh break a1-handout not_found
```

Speaker, while waiting (~25 s): the fake course site now returns 404 for the handout. A single
failure is not an alert: it becomes SUSPECT and is rechecked sooner; only 3 failures in a row make
it DOWN, which avoids false alarms.

Both browsers turn **SUSPECT**, then **DOWN** without a refresh; window 4 shows the open incident.

> Advanced feature 1, real-time: the worker issues a Postgres NOTIFY when the state changes, every
> API replica receives it and pushes the new state over WebSockets, so it does not matter which
> replica a browser is connected to.

```bash
scripts/demo.sh mail
```

> Advanced feature 2, notifications: one email per course member. The email is written to an
> outbox in the same transaction as the state change, so an alert exists if and only if the state
> change committed. This is a mock mail endpoint; the DigitalOcean Function plus SendGrid comes next.

Then restore it, so it recovers during the next steps:

```bash
scripts/demo.sh fix a1-handout
```

### 2:15–2:55 Live 2: orchestration

```bash
scripts/demo.sh services
```

> Eight services on three nodes; API with 3 replicas, workers with 2. All images are tagged with
> the git commit they were built from.

```bash
scripts/demo.sh kill-api
```

> I just killed one API container. Swarm sees 2 of 3 and starts a replacement; the site keeps
> answering 200 because the other replicas serve it. (Point at Browser B: still live.)

### 2:55–3:40 Live 3: persistence

```bash
scripts/demo.sh restart-db
```

It prints the data directory on the Volume and counts of users, resources, incidents, check
results, emails and reports. It then replaces the Postgres container while polling the public site:
`/health` goes 503 for about 10 s, and a new container ID appears. The same counts come back.

> State lives only in Postgres, and Postgres keeps its files on a DigitalOcean Volume, not in the
> container. A new container on the same Volume has all the data. We also rebooted the whole
> manager node: back in 52 s, nothing lost. If the Volume is missing, Postgres refuses to start
> rather than starting empty.

The handout has recovered by now: Browser B shows HEALTHY, and `scripts/demo.sh mail` would show
the "is back" emails (skip if short on time).

### 3:40–4:10 Live 4: monitoring and logs

```bash
scripts/demo.sh logs worker
```

> Every check is one JSON log line: resource, outcome, status, latency, new state. You can see the
> 404s and the recovery from a minute ago.

Window 6: DigitalOcean **Uptime** check on `/health` from three regions (with down, latency and
certificate-expiry alerts) and **Alerts** for CPU, memory, disk and load on all three nodes.
Backup: screenshots on the slide.

### 4:10–5:20 Advanced features and completion plan (slides 3–4)

| Feature | Status | Evidence / plan |
|---|---|---|
| Real-time updates (LISTEN/NOTIFY → WebSockets on every replica) | Done | Live 1 |
| Notifications: transactional outbox + dispatcher | Done | Crash test: dispatcher killed after sending, before recording → resent once with the same Message-ID, receiver dropped the duplicate; no alert lost when the outbox insert fails |
| Notifications: DigitalOcean Function + SendGrid | Planned | Dispatcher already posts to `MAIL_ENDPOINT`; switch it to the Function URL, key in the Function, not the cluster |
| Security: Argon2 passwords, course roles, HTTPS (Let's Encrypt), Docker secrets | Done | Student gets 403 on admin actions; secrets never in the stack file |
| Security: encrypted staff API tokens | State plainly whether it is done, planned with a date, or cut and why |

Completion plan (dates up to Dec 5): Function + SendGrid; resource edit/delete for staff; student
email opt-in; application metrics (checks per minute, failure rate, queue lag); load test with 2 vs
4 workers; the experiment on which failures HTTP responses can and cannot distinguish.

### 5:20–6:00 Failover evidence and limitations (slide 5)

- Rehearsed on DigitalOcean: rolling update, worker killed mid-check, node powered off and back.
  **943 public requests during these, 0 failed**; a dead node's work moved in 7 to 16 s.
- Limitation: the manager runs Postgres and Nginx, so it is a single point of failure; accepted for
  this scope, Volume and fast reboot mitigate it.

## Local fallback (no cloud or no Wi-Fi)

Core requirements may be shown locally. On the same laptop, before class:

```bash
docker compose up -d --build --wait
```

Then sign in at http://localhost/dashboard/ as the local demo admin, open **Administration**, create
course `ece1779`, assign yourself **staff** on it, and register the same `a1-handout` resource. Only
course members get emails, so locally the mailbox shows one email per member you assigned.
(Tested: DOWN about 22 s after the break, email delivered, data intact after restarting Postgres.)

| Live step | Local equivalent |
|---|---|
| Dashboard | http://localhost/dashboard/, demo admin `admin@edupulse.example.com` / `ece1779edupulse` (local only) |
| Break / fix | `scripts/fake.sh set a1-handout not_found`, `scripts/fake.sh ok a1-handout` |
| Emails | http://localhost:8003/messages |
| Services | `docker compose ps` (Compose; Swarm evidence from the recording) |
| Persistence | `docker compose restart postgres`, then the dashboard still shows everything |
| Logs | `docker compose logs --tail 20 worker` |

## Likely questions (everyone should be able to answer)

| Question | Short answer |
|---|---|
| Why Docker Swarm, not Kubernetes? | Three small Droplets and one team-sized stack; Swarm gives replicas, rolling updates, secrets and placement with far less configuration. Trade-off: fewer features (no autoscaling). |
| Why PostgreSQL as the job queue, not Redis? | Checks are low volume and must not be lost; Postgres already gives durability, `SKIP LOCKED` claims and `LISTEN/NOTIFY`. One fewer service. Trade-off: a throughput ceiling at large scale. |
| How do two workers avoid checking the same resource? | They claim rows with `FOR UPDATE SKIP LOCKED` and a 30 s lease; a stale worker's result is discarded by a lease token. Tested: 0 duplicate checks. |
| What if a worker dies mid-check? | Its lease expires after 30 s and another worker takes the resource. Shown on the cloud: recovered 37 s after the kill. |
| What if the manager goes down? | Postgres and Nginx stop until it is back: our single point of failure. Data is on the Volume; a reboot took 52 s. |
| Can an alert be lost or sent twice? | Not lost: written in the same transaction as the state change. Possibly sent twice after a crash, with the same Message-ID so the receiver can drop it. |
| How do you avoid false alarms? | DOWN only after 3 consecutive failures; SUSPECT is rechecked sooner. A site failing every third request never alerted. |
| How is a login wall detected? | A redirect to a login page usually ends in 200, so we look at where the redirect ended, not just the status code. |
| What do the monitoring alerts tell you? | Node CPU, memory, disk and load; the uptime check covers the whole path (Nginx, API, database) and certificate expiry. |
| What is left? | The completion plan above. |

## Afterwards

```bash
scripts/demo.sh fix a1-handout && scripts/demo.sh remove a1-handout && scripts/demo.sh mail-reset
```
