# EduPulse

ECE1779 final project: course resource monitoring. See [docs/proposal.md](docs/proposal.md).

## Local quickstart

Requires Docker Desktop. From PowerShell at the repository root, start the local stack with the sample development credentials:

```powershell
docker compose --env-file .env.example up -d --build --wait
```

| Service | URL | Notes |
|---|---|---|
| Dashboard | http://127.0.0.1:8082/dashboard/ | Served through Nginx with WebSocket upgrades |
| API | http://127.0.0.1:8082/docs | Swagger UI through Nginx |
| Fake course site | http://127.0.0.1:8001/r/lecture5 | Switch modes with `PUT /admin/resources/{name}` and header `x-admin-token` |

Apply the migration below when starting with a fresh database. Stop the stack with `docker compose --env-file .env.example down` to keep the database volume.

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

The local Compose file enables unauthenticated writes for development and publishes Nginx and the fake site on loopback.

Apply the schema from the schema branch and add a demo course on a fresh database. Exporting the migration lets you run it without switching your working branch.

```powershell
$schemaDir = Join-Path $env:TEMP ("edupulse-schema-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $schemaDir | Out-Null
$archive = Join-Path $schemaDir "schema.zip"
git archive --format=zip --output=$archive origin/feat/database-schema alembic.ini db
Expand-Archive -LiteralPath $archive -DestinationPath $schemaDir
docker compose --env-file .env.example run --rm --no-deps -v "${schemaDir}:/migration:ro" -w /migration api sh -ec "pip install -q alembic psycopg2-binary && alembic upgrade head"
docker compose --env-file .env.example exec -T postgres psql -U edupulse -d edupulse -v ON_ERROR_STOP=1 -c "INSERT INTO courses (id, name) VALUES ('demo', 'Demo Course') ON CONFLICT (id) DO NOTHING"
```

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
