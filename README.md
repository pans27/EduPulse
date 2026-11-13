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
| API | http://localhost:8000/health | Placeholder until the real API lands |
| Fake course site | http://localhost:8001/r/lecture5 | Switch modes with `PUT /admin/resources/{name}` and header `x-admin-token` |
| PostgreSQL 18 | localhost:5432 | Credentials from `.env`; data in the `pgdata` volume |

`docker compose down` keeps the database; `docker compose down -v` deletes it.

## Repository layout

| Path | Owner | Contents |
|---|---|---|
| `services/api` | Carla | FastAPI REST, auth, WebSocket |
| `services/worker` | Byan | Check worker (the dispatcher reuses this image) |
| `services/fake-site` | Yuan | Controllable test target |
| `infra/` | Yuan | Swarm stack, Nginx, deployment scripts |
| `docs/` | All | Proposal and report material |
