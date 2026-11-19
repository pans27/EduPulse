# EduPulse

ECE1779 final project: course resource monitoring. See [docs/proposal.md](docs/proposal.md).

## Local quickstart

Requires Docker Desktop.

```bash
cp .env.example .env
docker compose up -d --build
```

| Service | URL | Notes |
|---|---|---|
| API | http://localhost:8000/health | PostgreSQL connectivity check |
| Fake course site | http://localhost:8001/r/lecture5 | Switch modes with `PUT /admin/resources/{name}` and header `x-admin-token` |
| PostgreSQL 18 | localhost:5432 | Credentials from `.env`; data in the `pgdata` volume |
| Nginx | http://localhost/health | Reverse proxy to the API, including WebSocket upgrades; same path layout as production |

`docker compose down` keeps the database; `docker compose down -v` deletes it.

## API development preview

Run the API with Python 3.12 from the repository root. Use one process on loopback. Preview data is reset on restart, and all preview routes are open to the local client.

```powershell
python -m pip install -r services/api/requirements.txt
$env:EDUPULSE_LOCAL_API = "1"
python -m uvicorn app.main:app --app-dir services/api --host 127.0.0.1
```

Open the [dashboard](http://127.0.0.1:8000/dashboard/) to browse courses, register resources, and report issues. Use [Swagger UI](http://127.0.0.1:8000/docs) to call the REST endpoints directly.

To run the same dashboard and API preview in Docker, use the commands below from the repository root and open the [Docker dashboard](http://127.0.0.1:8000/dashboard/).

```powershell
docker build -t edupulse-dashboard ./services/api
docker run --rm -d --name edupulse-dashboard -p 127.0.0.1:8000:8000 -e EDUPULSE_LOCAL_API=1 edupulse-dashboard
```

| Endpoint | Behavior |
|---|---|
| `POST /api/courses/{course_id}/resources` | Registers an HTTP(S) resource with a name, check interval in seconds, and optional timezone-aware release time. |
| `GET /api/courses/{course_id}/resources` | Lists resources and their current state. A new resource has a null state pending its first result. |
| `GET /api/courses/{course_id}/resources/{resource_id}` | Reads a single resource within its course. |
| `POST /api/courses/{course_id}/resources/{resource_id}/reports` | Records a description and makes the resource immediately due. |
| `WS /api/courses/{course_id}/ws` | Sends a `course.snapshot` on connect and when a resource is registered or reported. Reconnecting reads the current snapshot. |
| `GET /api/metrics` | Returns counts of registered resources and student reports in the preview. |

With preview mode disabled, `/health` checks PostgreSQL and the course API returns 503 pending the shared storage integration. The preview records due times for the dashboard. The worker integration will execute checks, apply the health state machine, and record check results and incidents.

For the Nov 15–21 real-time milestone, the cross-replica integration needs Byan's migration and the worker's PostgreSQL notification channel and resource ID payload. API replicas can then refresh the affected course from PostgreSQL and send the current snapshot. The Nov 25–Dec 5 authentication milestone adds Argon2 and course membership authorization before enabling shared access.

## API tests

Run from the repository root with Python 3.12. The tests use the in-memory preview and a mocked database pool.

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
