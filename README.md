# EduPulse

ECE1779 final project: course resource monitoring. See [docs/proposal.md](docs/proposal.md).

## Local quickstart

Requires Docker Desktop.

Create or update the ignored `docker-compose.local.yml` with these settings. Keep your existing project name if you already have a local database volume.

```yaml
name: edupulse-pg-preview
services:
  postgres:
    ports: !reset []
  migrate:
    environment:
      EDUPULSE_SEED_DEMO_ADMIN: "1"
  api:
    ports: !reset []
    environment:
      EDUPULSE_AUTH_INSECURE_COOKIE: "1"
      EDUPULSE_RESOURCE_NOTIFY_CHANNEL: resource_updates
  nginx:
    ports: !override
      - "127.0.0.1:8082:80"
  fake-site:
    ports: !override
      - "127.0.0.1:8001:8000"
```

Start the stack from Bash or PowerShell. Compose applies the PostgreSQL migrations before starting the API.

```sh
docker compose --env-file .env.example -f docker-compose.yml -f docker-compose.local.yml up -d --build --wait
```

| Service | URL |
|---|---|
| Dashboard | http://127.0.0.1:8082/dashboard/ |
| Swagger UI | http://127.0.0.1:8082/docs |
| Fake course site | http://127.0.0.1:8001/r/lecture5 |

To stop the stack and keep its database volume, use the same Compose options with `down`.

## API endpoints

| Endpoint | Behavior |
|---|---|
| `POST /api/courses/{course_id}/resources` | Registers an HTTP(S) resource with a name, check interval in seconds, and optional timezone-aware release time. |
| `GET /api/courses/{course_id}/resources` | Lists resources and their current state. A new resource has a null state pending its first result. |
| `GET /api/courses/{course_id}/resources/{resource_id}` | Reads a single resource within its course. |
| `POST /api/courses/{course_id}/resources/{resource_id}/reports` | Records a description and makes the resource immediately due. |
| `WS /api/courses/{course_id}/ws` | Sends a `course.snapshot` on connect and when a resource is registered or reported. Reconnecting reads the current snapshot. |
| `GET /api/metrics` | Returns counts of registered resources and student reports in PostgreSQL. |

## Database authentication

Local and deployed APIs require PostgreSQL-backed accounts. Login issues a random session cookie and stores its hash in PostgreSQL. Sessions expire after one hour, and logout revokes the session across API replicas. Staff can register resources in their assigned courses. Staff and students can view course resources, receive WebSocket updates, and submit reports. Global metrics require an admin account.

The auth migration seeds `admin@edupulse.example.com` with the demo password when `EDUPULSE_SEED_DEMO_ADMIN=1` is set. Users can register with an email address on the dashboard. Administrators can open the separate [Administration page](http://127.0.0.1:8082/dashboard/admin/) to create courses and assign users by email. Select a course for staff or students. The administrator role applies across EduPulse.

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

Use HTTPS for secure session cookies on Swarm. The local override allows cookies on loopback HTTP.

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
