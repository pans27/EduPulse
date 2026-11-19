# infra

Swarm stack file, Nginx configuration, and deployment scripts for DigitalOcean live here (owner: Yuan).

## Nginx (`nginx/default.conf`)

| Path | Behavior |
|---|---|
| `/` | Redirects to `/dashboard/` |
| `/api/courses/{id}/ws` | WebSocket upgrade, 1 h idle timeout |
| `/nginx-health` | Answered by Nginx itself; used by the container healthcheck |
| everything else | Proxied to `api:8000` with `X-Forwarded-*` headers, 30 s read timeout |

Nginx resolves `api` through Docker DNS (`127.0.0.11`) on each request instead of once at startup, so it can start before the API and keeps working when API containers are replaced. In Swarm, `api` resolves to the service's virtual IP, which balances across replicas.

After editing the config locally:

```bash
docker compose exec nginx nginx -t && docker compose exec nginx nginx -s reload
```

HTTPS (Certbot on the manager node) is added in a later change.
