# EduPulse live demo script

About 4 minutes of the 6-minute slot, on the DigitalOcean deployment. Every terminal step is one
`scripts/demo.sh` command; run `scripts/demo.sh` with no arguments for the list.

## What you need

| Item | Detail |
|---|---|
| Site | **https://138-197-173-188.sslip.io** (redirects to `/dashboard/`) |
| Staff account | `vianne.liu@mail.utoronto.ca`, assigned **staff** on `ece1779` |
| Student account | `yuanliu0922@gmail.com`, assigned **student** on `ece1779` |
| Admin account | `liuyuan9387@gmail.com` (also staff on `ece1779`) |
| Laptop | The one whose SSH key is on the Droplets (Yuan's). `demo.sh` uses SSH to run commands on the servers |
| Repo | A checkout of `main`, terminal in the repository root |

Passwords are the ones the account owners set; they are not stored in the repo.

## Screen layout

| Window | Content |
|---|---|
| **Browser A** (normal window) | Signed in as **staff** |
| **Browser B** (private / incognito window, so it has its own session cookie) | Signed in as **student** |
| **Terminal 1** | `scripts/demo.sh watch`: states, open incidents and the email outbox, refreshed every 2 s |
| **Terminal 2** | The commands below |

Put the two browsers side by side; the audience should see both change at the same time.

## 15 minutes before

```bash
cd ~/ECE1779/EduPulse && git switch main && git pull
```

```bash
scripts/demo.sh services          # all services full, our images on the same commit
```

```bash
scripts/demo.sh remove a1-handout # only if a previous rehearsal registered it ("removed …")
```

```bash
scripts/demo.sh remove dataset
```

```bash
scripts/demo.sh fix a1-handout && scripts/demo.sh fix dataset
```

```bash
scripts/demo.sh mail-reset        # empty mailbox
```

Then sign in on both browsers (**Sign in to EduPulse** → Email, Password → **Sign in**), select
course **ECE1779** in the top-right **Course** menu, and start `scripts/demo.sh watch` in Terminal 1.
Check that both browsers say **Live updates connected** under the heading.

## The demo

### 1. Staff registers a resource (0:00, Browser A)

In **Register a resource** on the right:

| Field | Value |
|---|---|
| Name | `Assignment 1 handout` |
| Resource URL | `http://fake-site:8000/r/a1-handout` |
| Check interval in seconds | `10` |
| Expected release (optional) | leave empty |

Click **Register**. It appears as **Awaiting first check**, then **HEALTHY** within a couple of
seconds, in **both** browsers without a refresh.

> Say: staff register course resources; a pool of workers checks them. Both dashboards update
> live over WebSockets, even though they may be connected to different API replicas.

### 2. Student view (0:40, Browser B)

Point out: no **Register a resource** form for students; each resource has **Report issue**.

> Say: roles come from course membership; the API enforces them, not just the page.

### 3. The handout disappears (1:00, Terminal 2)

```bash
scripts/demo.sh break a1-handout not_found
```

Within ~10 s it turns **SUSPECT**, then **SUSPECT** again, then **DOWN** about 20–30 s after the
break. Terminal 1 shows the failure count climbing and an open incident `DOWN | HTTP 404`.

> Say: one failed check never alerts; we wait for 3 in a row, rechecking a suspect resource sooner
> than usual. Our fake course site lets us inject the failures.

### 4. The alert emails (1:40, Terminal 2)

```bash
scripts/demo.sh mail
```

Shows one email per course member: `[EduPulse] Assignment 1 handout is DOWN`, with course, URL,
`SUSPECT -> DOWN`, `HTTP 404`, and its Message-ID.

> Say: the alert is written to an outbox in the same database transaction as the state change, so
> an alert exists if and only if the change happened. A separate dispatcher sends it. If the
> dispatcher crashes after sending but before recording it, the email is sent again with the same
> Message-ID, and the receiver drops the duplicate (we tested that crash; slide N).
> This is a mock mail endpoint; the real path is a DigitalOcean Function and SendGrid.

### 5. A student spots a problem first (2:10)

Terminal 2, a resource that is only checked every 10 minutes:

```bash
scripts/demo.sh fix dataset
```

Browser A registers **Name** `Dataset`, **Resource URL** `http://fake-site:8000/r/dataset`,
**Check interval** `600` → HEALTHY. Then break it:

```bash
scripts/demo.sh break dataset server_error
```

Nothing changes on the dashboard: the next check is 10 minutes away (Terminal 1 shows
`next check in 09:5x`). In Browser B click **Report issue** on Dataset → Description
`The dataset link returns an error` → **Submit report** → "Report submitted. Recheck requested."
It turns **SUSPECT** within a few seconds and **DOWN** about 30 s later.

> Say: a report makes the resource due immediately, even if a check is already running.

### 6. Recovery (3:00, Terminal 2)

```bash
scripts/demo.sh fix a1-handout
```

Within ~10 s **HEALTHY**; the incident closes (Terminal 1); `scripts/demo.sh mail` now also shows
`… is back (HEALTHY)`.

> Say: only a successful check closes an incident; a timeout after a login wall would not.

### 7. Infrastructure (3:20, Terminal 2)

```bash
scripts/demo.sh services
```

Three Droplets in a Docker Swarm: API ×3, workers ×2, dispatcher, Postgres on a DigitalOcean
Volume, Nginx with Let's Encrypt.

```bash
scripts/demo.sh kill-api
```

Kills an API container; replicas go `2/3` and back to `3/3` in about 20 s while the site keeps
answering `200` and the dashboards stay live.

> Say: in our failover rehearsal we also powered off a whole node: Swarm moved its workers in 7 s
> and 507 out of 507 requests succeeded (slide N).

Optional, if time: DigitalOcean console → **Monitoring** → Uptime check and alert policies.

## Other stories to try

| Story | Command | What you see |
|---|---|---|
| Material behind a login | `break a1-handout login_redirect` | AUTH_REQUIRED after one check; a 302 to /login ends in 200, so a status code alone would say healthy |
| Flaky site, no false alarm | `break a1-handout fail_every_k k=3` | Occasional SUSPECT, never DOWN, no email |
| Random flapping | `break a1-handout flapping fail_rate=0.3` | Mostly HEALTHY/SUSPECT |
| Slow server | `break a1-handout slow delay_ms=4000` | DEGRADED, no email |
| Server refuses HEAD | `break a1-handout head_not_allowed` | Stays HEALTHY (falls back to GET) |
| Login wall, then the server dies | `login_redirect`, then `server_error` | Incident stays open through SUSPECT, then the same incident moves to DOWN |
| Missed release | Register with **Expected release** 2 minutes ahead and the resource broken (`not_found`) | Not checked before the release; after it, 3 × 404 → **MISSING** |
| Early publish | Same, but `fix` before the release time | HEALTHY at the release time |
| Student oversteps | In Browser B, open `/dashboard/admin/`, or POST a resource in `/docs` | 403 |

`scripts/demo.sh modes` lists the fake-site resources and their current modes.

## Showing the database

```bash
scripts/demo.sh db
```

Opens Adminer through an SSH tunnel at **http://localhost:8081** (it listens only on the manager's
loopback, never on the internet) and prints the login: System **PostgreSQL**, Server `postgres`,
Username `edupulse`, Database `edupulse`, and the password. Ctrl-C closes the tunnel and stops it.

Good tables to show: `resources` (state, failure count, `lease_owner` / `lease_until` while a check
runs), `check_results` (every check), `incidents`, `notification_outbox` (`pending` → `sent`,
`attempts`), `student_reports`.

## Afterwards

```bash
scripts/demo.sh fix a1-handout && scripts/demo.sh fix dataset
```

```bash
scripts/demo.sh remove a1-handout && scripts/demo.sh remove dataset
```

```bash
scripts/demo.sh mail-reset
```

## If something goes wrong

| Symptom | Check |
|---|---|
| "Live updates disconnected" | Refresh the page; `scripts/demo.sh services` |
| Register fails with 409 | That URL is already registered in the course: `scripts/demo.sh remove <name>` |
| A state does not change | `scripts/demo.sh modes` (did the break apply?), Terminal 1 (`next check in`) |
| A service below its replica count | `ssh root@138.197.173.188 docker service ps edupulse_<service>`, last column |
| Network fails during the talk | Switch to the pre-recorded run of this script |
