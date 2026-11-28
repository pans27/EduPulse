# EduPulse presentation: operator runbook

6 minutes, strictly enforced, then about 2 minutes of Q&A. The plan follows the presentation rubric:
**Clarity & Communication (6)**, **Technical Progress & Demonstration (10)** (containers,
persistence, orchestration, monitoring) and **Advanced Features & Completion Plan (4)**.

What each speaker says, slide by slide, is in [speaker-script.md](speaker-script.md); the same text
is in the speaker notes of the deck. This file is the operator's side: windows, checks before our
turn, the command for each slide, the local fallback and short answers for Q&A.

## Roles and setup

| Slides | Speaker | Yuan operates |
|---|---|---|
| 1–2 | Carla | |
| 3 | Byan, 6 clicks on the animated diagram | `break` at click 2 |
| 4 | Carla | browsers, inbox, `fix` |
| 5–7 | Yuan | terminal and browser |
| 8 | Carla | |
| 9–10 | Byan | |

- **One laptop** (Yuan's: the only one with SSH access to the Droplets), display **mirrored**, not
  extended. Arrive at 12:05 to test the projector (USB-C or HDMI).
- **Rule for every switch** (slide, terminal, browser, speaker): say first what we are about to do
  and why, then switch.

### Windows, open before our turn

Show each thing where it naturally lives: the product (dashboards, inbox) in the browser, the
infrastructure (containers, Swarm, logs) in the terminal.

| Window | Content | Shown on slide |
|---|---|---|
| Slides | `EduPulse-Team26.pptx` | all |
| Browser A (normal) | https://138-197-173-188.sslip.io signed in as **staff** (`vianne.liu@mail.utoronto.ca`), course **ECE1779** | 4, 5, 6 |
| Browser B (private window) | Same site, signed in as **student** (`yuanliu0922@gmail.com`), course **ECE1779** | 4 |
| Browser tab | A course member's real inbox, signed in: the student's Gmail (`yuanliu0922@gmail.com`). Alerts arrive through the DigitalOcean Function and SendGrid | 4 |
| Browser tab | DigitalOcean console → Monitoring (Uptime check, Alerts), already signed in | 7, optional |
| Terminal, **shown** | Repository root, font about **20 pt**, `clear` before each command; history preloaded so it is ↑ and Enter | 5, 6, 7 |
| Terminal, **not shown** | Second tab for `break` and `fix` | 3, 4 |
| Backup | Short recordings of each live step, and screenshots, in case the cloud or Wi-Fi fails | |

### 30 minutes before

```bash
cd ~/ECE1779/EduPulse && git pull
```

```bash
scripts/demo.sh services
```

All services at full replicas, our five images on one commit (the tag on slide 5).

```bash
scripts/demo.sh fix a1-handout
```

In the inbox tab, delete earlier EduPulse emails so only the new alert shows. We send without
domain authentication, so check the spam folder once and mark an EduPulse email "Not spam".

```bash
scripts/demo.sh status
```

Only **Assignment 1 handout** is registered in ECE1779, and it is HEALTHY. If it is missing, register
it in Browser A: Name `Assignment 1 handout`, Resource URL `http://fake-site:8000/r/a1-handout`, Check
interval `10`, Expected release empty. Both browsers show "Live updates connected".

```bash
scripts/demo.sh ws
```

Two replicas should show 1 open WebSocket each, so slide 4 shows the NOTIFY reaching more than one
replica. If one replica shows 2, reload Browser B and run it again. Note which replicas they are: the
speaker names them on slide 4.

Check that all three API replicas are listening for state changes (expect `3`):

```bash
echo "SELECT count(*) FROM pg_stat_activity WHERE query = 'SELECT 1' OR query ILIKE 'LISTEN \"resource_updates\"%';" | ssh root@138.197.173.188 'docker exec -i $(docker ps -q -f name=edupulse_postgres) psql -U edupulse -d edupulse -Atq'
```

The listener heartbeat is `SELECT 1` every 10 s, so that is usually the last query on these
connections.

## The 6 minutes: operator commands

| Slide | When | Operator |
|---|---|---|
| 3 | Byan's click 2 ("two workers") | Hidden tab: `scripts/demo.sh break a1-handout not_found`. Starting here means DOWN arrives during slide 4. |
| 4 | Carla: "Left: staff, right: a student" | Browser A and B side by side. SUSPECT, then DOWN on both, no refresh. |
| 4 | "Did everyone get told?" | Inbox tab: one alert email per course member. |
| 4 | After the inbox | Hidden tab: `scripts/demo.sh fix a1-handout`; back to the slides. It recovers during slides 5–7. |
| 5 | "We'll replace the database container…" | Shown terminal: `scripts/demo.sh restart-db` (about 15 s), back to the slide; then terminal for the result (new container ID, same counts), then Browser A. Don't reload a dashboard while `/health` is 503 (about 6 s). |
| 6 | "First, what we asked Swarm for" | `clear`, `scripts/demo.sh services`, then right away `scripts/demo.sh kill-api` (about 19 s: 2/3 → 3/3, `/health` 200 throughout). |
| 7 | "Let's read the logs of the outage you just saw" | `clear`, `scripts/demo.sh logs worker`: the slide-4 outage, healthy runs folded. DigitalOcean tab only if ahead of time. |

At the 1-minute hand signal (5:00) we should be on slide 8 or 9. If a live step fails, switch to its
recording at once; troubleshooting counts toward the 6 minutes.

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
| What happens to live updates when Postgres restarts? | Each API replica pings its LISTEN connection every 10 s and reconnects if it doesn't answer, then sends browsers a full snapshot. Rehearsed: all 3 back within 6 s of a database replacement. |
| Can an alert be lost or sent twice? | Not lost: written in the same transaction as the state change. Sent twice only if the dispatcher dies after SendGrid accepted it but before marking it sent; the resend carries the same outbox id as its Message-ID. Our mock sink drops it, but Gmail shows both (tested). Exactly-once would need the Function to remember sent ids. |
| Why a DigitalOcean Function for email? | The SendGrid key lives in the Function, not in our cluster, and it runs only when there is an alert. |
| How do you avoid false alarms? | DOWN only after 3 consecutive failures; SUSPECT is rechecked sooner. A site failing every third request never alerted. Exception: a 401/403 alerts at once (a known limit). |
| How is a login wall detected? | A redirect to a login page usually ends in 200, so we look at where the redirect ended, not just the status code. |
| How are staff API tokens protected? | Encrypted in PostgreSQL with a key held as a Docker secret, and never returned by the API. |
| What do the monitoring alerts tell you? | Node CPU, memory, disk and load; the uptime check covers the whole path (Nginx, API, database) and certificate expiry. |
| What is left? | Slide 10: each item with an owner and a date before Dec 5. |

## Afterwards

```bash
scripts/demo.sh fix a1-handout
```
