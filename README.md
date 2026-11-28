# EduPulse

ECE1779 final project: course resource monitoring. See [docs/proposal.md](docs/proposal.md).

## Local quickstart

Requires Docker Desktop and Python. From the repository root, copy `.env.example` to `.env` if needed and generate the local resource token key once. Keep the same key across restarts. Compose applies the PostgreSQL migrations before starting the API.

```sh
cp .env.example .env
python -c "from pathlib import Path; import base64, secrets; key = Path('secrets/resource_token_key'); key.parent.mkdir(exist_ok=True); key.exists() or key.open('xb').write(base64.urlsafe_b64encode(secrets.token_bytes(32)))"
docker compose up -d --build --wait
```

| Service | URL |
|---|---|
| Dashboard | http://localhost/ |
| Swagger UI | http://localhost/docs |
| Fake course site | http://localhost:8001/r/lecture5 |

Compose binds published ports to loopback. The ignored `docker-compose.local.yml` is optional for personal ports or a different project name. Use `docker compose down` to stop the stack and keep the database volume.

## API endpoints

| Endpoint | Behavior |
|---|---|
| `GET /live` | Confirms FastAPI is running. Used by the API container healthcheck. |
| `GET /health` | Checks PostgreSQL connectivity and returns 503 if it is unavailable. |
| `POST /api/courses/{course_id}/resources` | Registers an HTTP(S) resource with a name, check interval in seconds, optional timezone-aware release time, and optional bearer token. |
| `GET /api/courses/{course_id}/resources` | Lists resources and their current state. A new resource has a null state pending its first result. |
| `GET /api/courses/{course_id}/resources/{resource_id}` | Reads a single resource within its course. |
| `GET /api/courses/{course_id}/email-preference` | Reads the signed-in member's incident email preference for the course. |
| `PUT /api/courses/{course_id}/email-preference` | Lets students set their own preference with `{"email_opt_in": true}` or `false`. |
| `PATCH /api/courses/{course_id}/resources/{resource_id}` | Lets course staff edit settings, replace a bearer token, or clear it with `"api_token": null`. |
| `DELETE /api/courses/{course_id}/resources/{resource_id}` | Lets course staff permanently delete a resource and its checks, incidents, reports, and queued alerts. |
| `POST /api/courses/{course_id}/resources/{resource_id}/reports` | Records a description and makes the resource immediately due. |
| `WS /api/courses/{course_id}/ws` | Sends a `course.snapshot` on connect and when a resource is registered or reported. Reconnecting reads the current snapshot. |
| `GET /api/metrics` | Returns counts of registered resources and student reports in PostgreSQL. |

## Database authentication

Local and deployed APIs require PostgreSQL-backed accounts. Login issues a random session cookie and stores its hash in PostgreSQL. Sessions expire eight hours after sign-in, and logout revokes the session across API replicas. Staff can register resources in their assigned courses. Staff and students can view course resources, receive WebSocket updates, and submit reports. Global metrics require an admin account.

Local Compose sets `EDUPULSE_LOCAL_TESTING=1` to enable demo admin seeding, session cookies over loopback HTTP, and HTTP bearer-token checks to the internal fake site. Swarm leaves this flag unset. On a fresh database, the auth migration seeds `admin@edupulse.example.com` with password `ece1779edupulse`. Users can register with an email address on the dashboard. Administrators can open the separate [Administration page](http://localhost/dashboard/admin/) to create courses and assign users by email. Select a course for staff or students. The administrator role applies across EduPulse.

Use addresses such as `student@example.com` for test accounts. Email validation rejects special-use domains such as `.local` and `.test`.

Students can opt in or out of incident emails from the dashboard for each course. They start opted out. Active staff always receive incident emails for their courses. Preference changes apply to newly queued alerts.

### Bootstrap the production administrator

After the Swarm database migration succeeds, find a running API replica from the manager:

```sh
docker service ps edupulse_api --filter desired-state=running
```

SSH into the node running that replica, find the container ID, and replace the example email with the administrator's email.

```sh
docker ps --filter label=com.docker.swarm.service.name=edupulse_api
docker exec -it <api-container-id> /usr/local/bin/db-env.sh \
  python -m app.provision_user admin@example.com --admin
```

The cloud stack leaves demo seeding disabled. The entrypoint reads the database credentials from the mounted Swarm secret, and the CLI prompts for a unique production password and stores its Argon2 hash. Bootstrap this account before allowing public registration. Rerunning the CLI preserves existing credentials and privileges. Sign in with the admin email to create courses and assign roles on the **Administration** page.

Course updates use `EDUPULSE_RESOURCE_NOTIFY_CHANNEL` with a plain-text resource UUID. The API refreshes WebSocket snapshots from committed PostgreSQL rows. The worker currently checks database connectivity while the monitoring implementation is pending.

Use HTTPS for secure session cookies on Swarm. Local Compose allows cookies on loopback HTTP.

## Bearer tokens for monitored resources

Course staff can supply a token when registering or editing a resource on a service that permits this use. EduPulse encrypts it with Fernet before writing it to PostgreSQL. The token is write-only, and the worker sends it as `Authorization: Bearer <token>` over HTTPS. Use the final URL because authenticated checks stop at redirects. Changing the URL clears the saved token unless the same patch supplies a replacement.

On the Swarm manager, create the `resource_token_key` Docker secret once before deploying a stack that includes resource authentication. Keep a secure backup of the key for database restores.

```sh
openssl rand -base64 32 | tr '+/' '-_' | docker secret create resource_token_key -
```

For local testing, start Compose using the quickstart and register `http://fake-site:8000/r/token-protected` in the dashboard with the fixture token `demo-resource-token`. The worker should report **HEALTHY** after a successful check. Use the editor to replace it with an incorrect token and confirm the worker reports **AUTH_REQUIRED** after another check. Local Compose permits HTTP bearer checks only to the internal fake site.

## API tests

Run from the repository root with Python 3.12. The tests use mocked PostgreSQL sessions.

```powershell
python -m pip install -r services/api/requirements-test.txt
Push-Location services/api
python -m unittest discover -s tests -v
Pop-Location
```

## Repository layout

| Path | Owner | Contents |
|---|---|---|
| `services/api` | Carla | FastAPI REST, auth, WebSocket |
| `services/worker` | Byan | Check worker (the dispatcher reuses this image) |
| `services/fake-site` | Yuan | Controllable test target |
| `infra/` | Yuan | Swarm stack, Nginx, deployment scripts |
| `docs/` | All | Proposal and report material |
