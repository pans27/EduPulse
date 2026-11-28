# EduPulse: speaker script (Team 26)

6:00 hard stop, then 2 min Q&A. Target **5:45**, which leaves 15 s of slack.
**Bold** = stress this word or phrase (slow down slightly, look up from the slides).
**Before every switch** (new slide, terminal, browser, new speaker), say first **what we're about to
do and why**, then switch. The audience should never look at a new window without knowing why.
Lines starting with ▶ are operator cues for Yuan; they are not spoken.

| # | Slide | Speaker | Budget | Ends at |
|---|---|---|---|---|
| 1–2 | EduPulse · We catch broken course links first | Carla | 0:35 | 0:35 |
| 3 | Architecture · One check, end to end | Byan | 0:50 | 1:25 |
| 4 | Core feature · Break a link, watch the alert | Carla (Yuan operates) | 0:45 | 2:10 |
| 5 | Containers & persistence · Replace any container, keep every row | Yuan | 0:40 | 2:50 |
| 6 | Orchestration · Kill a container, Swarm replaces it | Yuan | 0:35 | 3:25 |
| 7 | Monitoring · Is it up? What happened? | Yuan | 0:30 | 3:55 |
| 8 | Advanced features · Live updates, reliable alerts, security | Carla | 0:50 | 4:45 |
| 9 | Evidence · It survives a node failure and scales | Byan | 0:30 | 5:15 |
| 10 | What's left · Completion plan to Dec 5 | Byan | 0:30 | 5:45 |
| 11 | Questions | All | (Q&A) | |

**Checkpoints:** at the **1-minute hand signal** (5:00) we should be on slide 8 or 9. If we're
behind at 2:10, skip the optional sentences marked *(optional)*.

## Stage setup (Yuan)

The rule: **show each thing where it naturally lives.** The product (dashboards, inbox) is shown in
the browser; the infrastructure (containers, Swarm, logs) in the terminal, because that is its real
interface and live output proves this is a running cluster, not a slide.

| Window | What's in it | Shown on |
|---|---|---|
| Browser A | Staff dashboard, ECE1779 | 4, 5, 6 |
| Browser B | Student dashboard, ECE1779 (different API replica: `demo.sh ws`) | 4 |
| Browser tab | A course member's real inbox (the student's Gmail), old EduPulse emails deleted | 4 |
| Browser tab | DigitalOcean console, Uptime and Alerts (signed in) | 7, optional |
| Terminal, **shown** | `~/ECE1779/EduPulse`, font about **20 pt**, `clear` before each command | 5, 6, 7 |
| Terminal, **not shown** | A second tab for `break` and `fix` | 3, 4 |

- Load the shown terminal's history beforehand (run each once), so on stage it's **↑ and Enter**, no typing.
- Don't reload a dashboard during `restart-db`: the API answers 503 for about 6 s.
- `restart-db` is safe to run any time: since the listener heartbeat (deployed Nov 24) the API replicas
  re-LISTEN on the new Postgres by themselves (rehearsed: 3 of 3 back within 6 s).
- If asked how we inject the failure, switch to the hidden tab: it's one command, repeatable.

---

## Slides 1–2: We catch broken course links first (Carla, 0:35)

Hi, we're Team 26: Yuan, Baiyang, and I'm Carla. This is **EduPulse**.

These are real Piazza posts from this term: the course website is down, an assignment file returns
403. Each time, **students found the problem before the staff did**.

EduPulse **catches it first**: staff **register course resources**, **workers check them
automatically**, everyone sees a **live dashboard**, and members get an **email when a resource goes
down or comes back**.

Baiyang will show how it's built.

---

## Slide 3: One check, end to end (Byan, 0:50, 6 clicks)

Byan clicks once at each ▶ number; the slide opens on the full diagram with nothing highlighted.

Everything runs in a **Docker Swarm on three DigitalOcean Droplets**. Let's follow **one check
through it**.

> ▶ click 1: Staff register a resource

Staff **register a resource**: through **Nginx**, to one of **three stateless API replicas**, saved
in **PostgreSQL**, our only state.

> ▶ click 2: Two workers claim due checks
> ▶ Yuan, at this click, in the hidden terminal tab: `scripts/demo.sh break a1-handout not_found`

**Two workers** claim due checks with a **lease**, so **no check runs twice**.

> ▶ click 3: A worker checks the course site

The worker checks the course site, and gets a **404**.

> ▶ click 4: One transaction saves it all

**One transaction** saves the result, the **new state**, the incident and the **email**, then sends a
NOTIFY.

> ▶ click 5: Every API replica pushes it live

**Every API replica hears it** and pushes it over **WebSocket**: staff and students see it **live**.

> ▶ click 6: The dispatcher emails every member

And the **dispatcher** emails every member through a **DigitalOcean Function and SendGrid.**
Now let's watch this path **live**. Carla?

*(Starting `break` at click 2, about 30 s before the handover, means DOWN arrives during slide 4.)*

---

## Slide 4: Break a link, watch the alert (Carla speaks, Yuan operates, 0:45)

Now we **break a course link on purpose** and watch EduPulse catch it. **Left: staff, right: a
student.**

> ▶ Yuan: switch to Browser A and B side by side (everything on this slide is in the browser)

Yuan made the fake site return **404 for the assignment handout**.

It's now **SUSPECT**, not down: **one failure is not an alert.** Only **three in a row** make it
DOWN.

*(Wait for DOWN. If it's already DOWN, skip straight on.)*

There: **DOWN on both screens, with no refresh.** And these two browsers are on **different API
replicas**, replica 2 and replica 3. *(Use the replicas from the pre-demo `demo.sh ws` check.)*

**Did everyone get told?**

> ▶ Yuan: switch to the inbox tab

Here's the inbox: **one alert email per course member.**

> ▶ Yuan, in the hidden terminal tab: `scripts/demo.sh fix a1-handout`; then back to the slides

Yuan **fixes the link**; it turns green on its own while we move on. That's what users see. **Yuan
will show why it survives failures.**

---

## Slide 5: Replace any container, keep every row (Yuan, 0:40)

We'll **replace the database container while the site is running**, and check that **nothing is
lost**.

> ▶ Switch to the shown terminal, run `scripts/demo.sh restart-db` (about 15 s), switch back to the slide
> and talk through boxes 1 to 3 while it runs

While that runs: our rule is **containers are disposable, data is not.**

*(Box 1, 2)* The app containers keep **no state**; **all state is in one container: PostgreSQL**,
which we run ourselves.

*(Box 3)* Its files sit on a **DigitalOcean Volume**; if it's not mounted, Postgres **refuses to start rather than starting empty.**

> ▶ Switch to the terminal for the result, then to Browser A

*(Terminal)* The database container was replaced: **new container ID, same row counts.**
*(Browser A)* And the dashboard **still shows every resource and state, with no reload.**

---

## Slide 6: Kill a container, Swarm replaces it (Yuan, 0:35)

Next: **what happens when an app container dies.** First, what we asked Swarm for.

> ▶ Terminal: `clear`, then `scripts/demo.sh services` (prints in about 1 s)

**We declare the state, Swarm keeps it.** *(Point at the green boxes on the left)* These are the
**three API replicas, spread over two nodes**, and the database pinned to the Volume node, all placed
by **node labels**, never by hand.

> ▶ `scripts/demo.sh kill-api` right away; it takes about 19 s, so keep talking over it

*(Right, step 2)* Now I kill one API container: **two of three.** *(Step 3)* I don't run anything else;
**Swarm sees the gap and starts a replacement.** Meanwhile the site keeps answering **200**, because
**the other replicas keep serving.** *(Point at Browser A beside the terminal: still live.)*

*(When it prints "back to 3/3")* **Back to three of three** in under 20 seconds, **zero failed
requests.**

---

## Slide 7: Is it up? What happened? (Yuan, 0:30)

**We'd know before students do.** Monitoring answers **two questions**.

*(Left, 1)* **Is it up?** DigitalOcean checks **/health from three regions**, and because /health
queries the database, **one check covers Nginx, the API and Postgres.**

*(Right, 2)* **What happened?** Let's **read the logs of the outage you just saw.**

> ▶ Terminal: `clear`, then `scripts/demo.sh logs worker`; back to the slides afterwards

Every check is **one JSON log line**: one 404, SUSPECT; three, **DOWN with four emails queued**;
then the recovery. **Carla will cover our advanced features.**

> ▶ *(optional, only if ahead of time)* Switch to the DigitalOcean console tab: Uptime, then Alerts

---

## Slide 8: Live updates, reliable alerts, security (Carla, 0:50)

**Real-time updates**, which you just saw: one NOTIFY, and **any browser, on any replica, sees the
change live.**

**Email notifications.** The alert is **saved in the same transaction as the state change**, so it
exists **if and only if the change committed.** We killed the dispatcher mid-send: **nothing lost**,
and the resend **kept the same Message-ID**, so a duplicate can be recognised. Emails go out through a **DigitalOcean Function
with SendGrid**, which keeps the SendGrid key **out of our cluster.**

**Security**: **Argon2** passwords, **course roles**, **HTTPS**, **Docker secrets**, and **encrypted
staff API tokens.**

Baiyang will show what we measured.

---

## Slide 9: It survives a node failure and scales (Byan, 0:30)

**Does it hold up under real failures and load?** We tested **on the cloud and locally**.

*(Left)* We **powered off a real DigitalOcean node**: **all 507 HTTPS requests succeeded**, and
Swarm had the third API replica back **22 seconds** after the Droplet went off.

*(Right)* In my independent worker evaluation, a persistent failure reached **DOWN in 31.6
seconds**, and a crashed worker's **lease was reclaimed**. And **four workers checked 39% faster
than two.**



---

## Slide 10: Completion plan to Dec 5 (Byan, 0:30)

**So what's left?** All of it **before December 5**.

**Real email** through the DigitalOcean Function, staff can **edit and delete resources**, and
**application metrics** like queue lag, and **encrypted staff API tokens**. We'll **repeat the load test** beyond a single run. And an experiment on **what an HTTP status code can't tell you.** *(optional)* Our main known
limit: the **manager node is a single point of failure.**

Thank you. We're happy to take questions.

---

## If something goes wrong

- **A live step fails:** say "here's the recording" and switch at once. Troubleshooting counts toward
  the 6 minutes.
- **DOWN is slow on slide 4:** Carla goes on to the NOTIFY sentence anyway, and points at the screen
  when it flips.
- **Running long:** drop the *(optional)* sentences first (the slide 10 "known limit" line is one; it's
  on the slide).
- **Q&A:** short answers for likely questions are in [demo-script.md](demo-script.md).
