# EduPulse

ECE1779 final project: course resource monitoring. See [docs/proposal.md](docs/proposal.md).

## Local quickstart

Requires Docker Desktop. From the repository root, run this command in Bash or PowerShell.

```sh
docker compose --env-file .env.example up -d --build --wait
```

| Service | URL | Notes |
|---|---|---|
| Dashboard | http://127.0.0.1/dashboard/ | Served through Nginx with WebSocket upgrades |
| API | http://127.0.0.1/docs | Swagger UI through Nginx |
| Fake course site | http://127.0.0.1:8001/r/lecture5 | Switch modes with `PUT /admin/resources/{name}` and header `x-admin-token` |

If you have an ignored `docker-compose.local.yml` override, use the command below and open the dashboard on the Nginx port configured in that file.

```sh
docker compose --env-file .env.example -f docker-compose.yml -f docker-compose.local.yml up -d --build --wait
```

To stop the stack and keep its database volume, use the same Compose options with `down`.

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

## PostgreSQL-backed Docker preview

The local override enables unauthenticated writes for development and binds Nginx and the fake site to loopback.

Fresh databases require the Compose migration service introduced by [PR #10](https://github.com/pans27/EduPulse/pull/10). With that change, Compose runs Alembic before starting the API. The dashboard lists courses once they are provisioned.

Registrations and reports persist in PostgreSQL through SQLAlchemy's async ORM. The API image includes mappings for the existing migration and uses asyncpg for its dedicated notification connection. Alembic remains responsible for schema changes. The worker currently checks database connectivity, so resources await their first check until the monitoring worker is integrated.

In database mode, set `EDUPULSE_RESOURCE_NOTIFY_CHANNEL` to the channel agreed with the worker. Local Compose uses `resource_updates` with a plain-text resource UUID. Each API replica listens on a dedicated connection and refreshes affected WebSocket snapshots from committed PostgreSQL rows. It also refreshes snapshots after reconnecting to PostgreSQL. Leave `EDUPULSE_DATABASE_WRITES` disabled outside local development until course authorization is implemented.

## API tests

Run from the repository root with Python 3.12. The tests use the in-memory preview and a mocked ORM session.

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
