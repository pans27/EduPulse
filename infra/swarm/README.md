# DigitalOcean Swarm cluster

Three Droplets in one VPC form a Docker Swarm. Swarm traffic stays on the private network; only Nginx on the manager is public.

| Droplet | Size | Swarm role | Node label | Runs |
|---|---|---|---|---|
| `edupulse-manager` | 2 GB / 1 vCPU ($12/mo) | manager | `db=true` | PostgreSQL (on the Volume), Nginx |
| `edupulse-node-1` | 1 GB / 1 vCPU ($6/mo) | worker | — | API replicas, check workers, dispatcher |
| `edupulse-node-2` | 1 GB / 1 vCPU ($6/mo) | worker | `fakesite=true` | API replicas, check workers, fake course site |

About $24/month plus the Volume (10 GB, about $1). Droplets bill while powered off; only destroying them stops the charge.

## 1. Before you start

- Confirm the team's DigitalOcean credit (e.g. the GitHub Student Pack) and create a **Team** so all three members can see the resources.
- Add each member's SSH public key under **Settings → Security → SSH keys** (`cat ~/.ssh/id_ed25519.pub`).

## 2. Create the Droplets

Create all three in one go (**Create → Droplets**, quantity 3, then rename) with:

| Setting | Value |
|---|---|
| Region | Toronto (TOR1); all three in the same region so they share the default VPC |
| Image | Ubuntu 24.04 LTS x64 |
| Size | Basic, Regular SSD; 2 GB for the manager, 1 GB for the two workers (create the manager separately if sizes differ) |
| Authentication | SSH keys (all members), no password |
| Monitoring | **Enabled** (installs the metrics agent; needed for alerts later) |
| User data | contents of [`cloud-init.yaml`](cloud-init.yaml) |
| Tags | `edupulse` on all three, plus `edupulse-manager` on the manager |

Cloud-init takes 2–3 minutes. On each Droplet, check:

```bash
ssh root@<public-ip> 'cat /var/log/edupulse-bootstrap.done; docker --version; free -h | grep Swap'
```

## 3. Cloud firewalls

**Networking → Firewalls**. Two firewalls, applied by tag so new nodes inherit them.

`edupulse-cluster` (tag `edupulse`):

| Inbound | Ports | Sources | Why |
|---|---|---|---|
| SSH | TCP 22 | All IPv4/IPv6 | Key-only login |
| Swarm management | TCP 2377 | tag `edupulse` | Join and manager API |
| Node gossip | TCP + UDP 7946 | tag `edupulse` | Membership |
| Overlay data | UDP 4789 | tag `edupulse` | VXLAN between containers |
| All | ICMP | tag `edupulse` | Debugging |

Outbound: allow all (image pulls, SendGrid, DO Functions).

`edupulse-public` (tag `edupulse-manager`): TCP 80 and 443 from all IPv4/IPv6.

The Docker-published ports (8000, 8001, 5432) are deliberately **not** opened: Docker writes its own iptables rules and bypasses `ufw`, so the cloud firewall, which sits outside the Droplet, is what actually keeps them private.

## 4. Form the Swarm

On the manager, use the **private** (VPC) address so Swarm traffic never leaves the VPC:

```bash
PRIVATE_IP=$(curl -s http://169.254.169.254/metadata/v1/interfaces/private/0/ipv4/address)
docker swarm init --advertise-addr "$PRIVATE_IP"
docker swarm join-token worker   # prints the join command
```

On each worker, run the printed command with its own private address:

```bash
PRIVATE_IP=$(curl -s http://169.254.169.254/metadata/v1/interfaces/private/0/ipv4/address)
docker swarm join --advertise-addr "$PRIVATE_IP" --token <token> <manager-private-ip>:2377
```

Back on the manager, label the nodes (placement constraints in the stack file use these):

```bash
docker node update --label-add db=true edupulse-manager
docker node update --label-add app=true edupulse-node-1
docker node update --label-add app=true --label-add fakesite=true edupulse-node-2
docker node ls
docker node inspect edupulse-manager edupulse-node-2 --format '{{.Description.Hostname}} {{.Spec.Labels}}'
```

## 5. Verify the cluster

Run from the manager. The test service spreads three replicas over the nodes and checks the routing mesh:

```bash
docker network create -d overlay --attachable smoke
docker service create --name smoke --replicas 3 --network smoke -p 8080:80 nginx:1.28-alpine
docker service ps smoke --format '{{.Name}} {{.Node}} {{.CurrentState}}'   # one replica per node
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080             # 200 via the mesh
docker service rm smoke && docker network rm smoke
```

Expected: `docker node ls` shows 3 nodes `Ready`/`Active`; the three replicas land on three different nodes; curl prints `200`. Port 8080 stays unreachable from the internet because no firewall rule allows it.

## 6. Deploy the stack

[`stack.yml`](stack.yml) runs the same services as Compose, with Swarm-only additions:

| Feature | How |
|---|---|
| Passwords stay out of the stack file | `pg_password` and `fake_admin_token` are Docker secrets, mounted at `/run/secrets/`. Postgres reads `POSTGRES_PASSWORD_FILE`; API and worker get `DATABASE_URL` from the [`db-env.sh`](db-env.sh) entrypoint shim |
| Persistent data | Postgres binds a directory on the DigitalOcean Volume (see below), pinned to the manager, with a 60 s stop grace period so it can shut down cleanly |
| Placement | Postgres on `db=true`, fake site on `fakesite=true`, API and workers on `app=true`, Nginx on the manager |
| Schema migrations | `migrate` is a `replicated-job`: it runs `alembic upgrade head` to completion on every `stack deploy` and retries (up to 10 times, 5 s apart) while Postgres is still starting. Swarm has no `depends_on`, so the API answers 503 until the tables exist. Re-running is a no-op at head |
| Zero-downtime API updates | `parallelism: 1`, `order: start-first`, automatic rollback on failure |
| Self-healing | Swarm restarts failed tasks; the image healthchecks mark unhealthy replicas |
| HTTPS | Let's Encrypt certificate from certbot on the manager, bind-mounted into Nginx; HTTP redirects to HTTPS (see below) |
| Real client IPs | Nginx publishes ports 80 and 443 in host mode (not the routing mesh); uvicorn runs with `--proxy-headers` |
| Config files across nodes | `nginx/https.conf`, `nginx/edupulse.inc` and `db-env.sh` ship as Swarm configs, not bind mounts. To change one, bump `NGINX_CONF_VERSION` / `DB_ENV_VERSION` and redeploy |

### PostgreSQL Volume

Postgres keeps its data on a 10 GB DigitalOcean Volume attached to the manager, so the data outlives the containers, the stack and the Droplet's own disk. Create and attach it once, from a laptop:

```bash
doctl compute volume create edupulse-pgdata --region tor1 --size 10GiB --fs-type ext4 --fs-label pgdata --tag edupulse
doctl compute volume-action attach <volume-id> <manager-droplet-id> --wait
```

Ubuntu mounts it automatically at `/mnt/edupulse_pgdata` through a systemd unit (`mnt-edupulse_pgdata.mount`, with `nofail`), and remounts it on boot. Then, on the manager:

```bash
mkdir /mnt/edupulse_pgdata/postgres
```

The stack binds `/mnt/edupulse_pgdata/postgres`, not the mount point itself. That subdirectory exists only on the Volume, so if the Volume is ever missing, Swarm rejects the Postgres task (`bind source path does not exist`) instead of starting an empty database on the Droplet's disk. To bring it back, use `systemctl start mnt-edupulse_pgdata.mount` (there is no fstab entry, so `mount` alone fails).

To move an existing database onto the Volume: `docker service scale edupulse_postgres=0`, then `cp -a` the old data directory into `/mnt/edupulse_pgdata/postgres/`, then redeploy.

On the manager, once:

```bash
openssl rand -hex 24 | tr -d '\n' | docker secret create pg_password -
openssl rand -hex 24 | tr -d '\n' | docker secret create fake_admin_token -
openssl rand -base64 32 | tr '+/' '-_' | docker secret create resource_token_key -
```

The API and worker share `resource_token_key` to encrypt and decrypt resource tokens. Keep a secure backup of this key for database restores.

Build and push the images from a laptop. The Droplets are amd64, so on an Apple Silicon Mac the platform flag is required. The registry is `registry.digitalocean.com/edupulse` (Basic plan, nyc3; DigitalOcean has no registry region in Toronto).

```bash
doctl registry login
R=registry.digitalocean.com/edupulse TAG=$(git rev-parse --short HEAD)
docker buildx build --platform linux/amd64 --provenance=false -t $R/api:$TAG --push services/api
docker buildx build --platform linux/amd64 --provenance=false -t $R/worker:$TAG --push services/worker
docker buildx build --platform linux/amd64 --provenance=false -t $R/fake-site:$TAG --push services/fake-site
docker buildx build --platform linux/amd64 --provenance=false -t $R/migrate:$TAG --push -f db/Dockerfile .
```

Give the manager read-only pull access (valid 30 days; repeat this before then), and copy the stack files:

```bash
doctl registry docker-config --expiry-seconds 2592000 | ssh root@<manager-ip> 'mkdir -p ~/.docker && umask 077 && cat > ~/.docker/config.json'
git archive HEAD infra | ssh root@<manager-ip> 'mkdir -p /opt/edupulse && tar -x -C /opt/edupulse'
```

Then on the manager. `--with-registry-auth` forwards the manager's credentials to the workers, so they need no login of their own:

```bash
cd /opt/edupulse
REGISTRY=registry.digitalocean.com/edupulse TAG=<git-sha> \
  docker stack deploy --with-registry-auth -c infra/swarm/stack.yml edupulse
docker stack services edupulse
```

Postgres only reads `POSTGRES_PASSWORD_FILE` when it initializes an empty data directory. Rotating the secret later also needs `ALTER USER edupulse PASSWORD ...`.

To re-run migrations without a full deploy: `docker service update --force edupulse_migrate`. Check the result with `docker service ps edupulse_migrate` (state `Complete`) and `docker service logs edupulse_migrate`.

### Tested locally (single-node Swarm on Docker Desktop, 2026-11-19)

| Check | Result |
|---|---|
| All services converge | api 3/3, worker 2/2, postgres, fake site, nginx 1/1 |
| `/health` through Nginx | 200, database ok, with the password only in the secret |
| `docker service inspect` | No password in the service environment |
| Kill one API container | 2/3 → 3/3 within 20 s |
| `docker service update --force edupulse_api` while polling `/health` 4×/s | 200/200 requests succeeded, update completed in about 40 s |

A first version capped API replicas at 2 per node; the start-first update then had no free slot for the new replica and hung. The label constraint replaced the cap.

### Migrate job (tested 2026-11-21, same setup)

| Check | Result |
|---|---|
| Fresh deploy | Job failed twice (`could not translate host name "postgres"`) while Postgres started, then completed; `alembic_version` = head, 5 tables |
| `/health` through Nginx after the job | 200, database ok |
| Same-spec redeploy and `--force` | Job re-ran and completed; no `Running upgrade` (no-op at head) |

### Deployed on DigitalOcean (2026-11-21, images from `6a904b3`)

| Check | Result |
|---|---|
| Swarm | 3 nodes `Ready`, private addresses 10.118.0.2–4, labels as above |
| Smoke test | One replica per node, 6/6 requests through the routing mesh returned 200, overlay reachable across nodes |
| Placement | Postgres, migrate and Nginx on the manager; API 2 + 1 and workers 1 + 1 across node-1 and node-2; fake site on node-2 |
| Migrate job | Failed twice while Postgres started, then `Complete`; `alembic_version` = head |
| `http://<manager-ip>/health` | 200, database ok; `/` redirects to `/dashboard/`; gzip on |
| Ports 8000, 8001, 5432, 2377, 8080 from the internet | Blocked by the cloud firewall |
| WebSocket: 6 clients over the public IP, spread 2 per API replica; one `UPDATE` + `pg_notify('resource_updates', id)` | All 6 received the pushed `DOWN` state, so updates reach clients on every replica; all still open after 70 s idle |

`cloud-init.yaml` must stay ASCII-only. A `→` in a comment reached the Droplets as an invalid control character, and cloud-init silently skipped the whole file (its status still said `done`), so check for `/var/log/edupulse-bootstrap.done` rather than trusting the status.

### Persistence rehearsal (DigitalOcean, 2026-11-22)

The existing database was moved onto the Volume first; its `system_identifier` was unchanged, so it is the same cluster. A marker row in `student_reports` was checked after each step:

| Scenario | Data | Recovery |
|---|---|---|
| `docker service update --force edupulse_postgres` (new container) | Marker present | Automatic |
| `docker stack rm edupulse`, then `stack deploy` (every container and network removed) | Marker present; migrate job a no-op | Automatic |
| Reboot the manager Droplet (Postgres, Nginx and the Swarm leader all go down) | Both markers present; Volume remounted on boot | Public `/health` OK after 52 s; API back to 3/3 about 30 s later |
| Volume unmounted, then Postgres started | Task rejected; nothing written to the Droplet's disk | After `systemctl start mnt-edupulse_pgdata.mount`, healthy with both markers |

While the database is down, the API's `/health` returns 503, so Swarm restarts every API replica. They come back once Postgres is healthy.

### Failover rehearsal (DigitalOcean, 2026-11-23, images from `ccdc62e`)

A laptop polled `https://…/health` 4 times a second through each scenario. A separate course with 6 fake-site resources (20 s interval, no members, so no emails) kept the workers busy; one of them answers in 8 s so a check can be interrupted mid-flight. The course was deleted afterwards.

| Scenario | What happened | `/health` |
|---|---|---|
| Rolling update of the API (`service update --force`, start-first) | 3 replicas replaced one at a time in 38 s | 212/212 OK, slowest 0.36 s |
| SIGKILL a worker 2 s into an 8 s check | Its lease expired 30 s after the claim; the other worker claimed the resource and recorded the result 37 s after the kill. Swarm restarted the killed task (exit 137) within about 6 s | not affected |
| Hard power-off of node-1 (1 API, 1 worker, the dispatcher) | Swarm marked the node down 6 s after the Droplet went off; the worker and dispatcher were running on node-2 7 s later, the third API replica 9 s after that. All 6 resources kept their 20 s check rhythm (largest gap 28.8 s, the 8 s resource) | 507/507 OK, slowest 0.40 s |
| Power node-1 back on | Rejoined as Ready 35 s after power-on. Swarm does not move tasks back by itself: everything stayed on node-2 until `service update --force` on the API and workers | 224/224 OK during the rebalance |

Findings:
- No request failed in any scenario. Postgres and Nginx are on the manager, which was not taken down; losing the manager is the single point of failure noted in the proposal.
- Swarm's replica count keeps counting a down node's tasks until the node returns (it showed `worker 3/2`), so `docker node ls` and `docker service ps` are the reliable views during an outage.
- After the rebalance both workers landed on node-1 again. Spreading them (`max_replicas_per_node: 1` with a stop-first update, which leases make safe) would keep one worker running through a single node failure instead of relying on rescheduling.

## 7. HTTPS

Live at **https://138-197-173-188.sslip.io**. sslip.io is a free DNS service that resolves `138-197-173-188.sslip.io` to `138.197.173.188`, so Let's Encrypt can issue a real certificate without us buying a domain. A purchased domain can replace it later: point an A record at the manager and issue a new certificate with the same `--cert-name`.

The Nginx configuration is split so local Compose stays plain HTTP:

| File | Used by |
|---|---|
| `infra/nginx/edupulse.inc` | Both: proxy settings, WebSocket and API locations |
| `infra/nginx/default.conf` | Local Compose, and the Swarm stack while bootstrapping: HTTP on port 80 |
| `infra/nginx/https.conf` | Swarm (default): port 80 serves only ACME challenges and `/nginx-health` and redirects the rest; port 443 has TLS 1.2/1.3 and HTTP/2 |

Nginx can't start `https.conf` before a certificate exists, so the first certificate is bootstrapped over HTTP, once, on the manager:

```bash
apt-get install -y certbot
mkdir -p /var/www/certbot /etc/letsencrypt
cd /opt/edupulse
NGINX_CONF_FILE=default.conf REGISTRY=... TAG=... docker stack deploy --with-registry-auth -c infra/swarm/stack.yml edupulse
certbot certonly --webroot -w /var/www/certbot -d 138-197-173-188.sslip.io --cert-name edupulse \
  --agree-tos --register-unsafely-without-email
REGISTRY=... TAG=... docker stack deploy --with-registry-auth -c infra/swarm/stack.yml edupulse   # back to https.conf
```

Renewal is automatic. Ubuntu's `certbot.timer` runs `certbot renew` twice a day, and renews through the same webroot that `https.conf` serves on port 80. The deploy hook `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh` then sends Nginx a `SIGHUP`, so it loads the new certificate without dropping connections:

```sh
#!/bin/sh
for c in $(docker ps -q -f name=edupulse_nginx); do docker kill -s HUP "$c" >/dev/null; done
```

### HTTPS checks (2026-11-22)

| Check | Result |
|---|---|
| Certificate | `CN=138-197-173-188.sslip.io`, issued by Let's Encrypt, valid until 2027-02-20 |
| `http://…/dashboard/` | 301 to `https://…/dashboard/` |
| `https://…/health` | 200, database ok, over HTTP/2; gzip on |
| `wss://…/api/courses/ece1779/ws` | Snapshot received |
| TLS 1.1 | Refused |
| `certbot renew --dry-run` | Success through the webroot |
| Container healthcheck (`http://127.0.0.1/nginx-health`) | Still served on port 80, no redirect |

## 8. Monitoring

DigitalOcean's metrics agent runs on all three Droplets (Monitoring enabled at creation). Alerts go to the account email; add teammates under each policy's recipients.

| Alert | Applies to | Condition |
|---|---|---|
| CPU | tag `edupulse` | Above 80% for 5 min |
| Memory | tag `edupulse` | Above 85% for 5 min |
| Disk | tag `edupulse` | Above 80% for 5 min |
| Load | tag `edupulse` | 1-minute load above 2 for 10 min (the nodes have 1 vCPU) |
| Uptime: down | `https://138-197-173-188.sslip.io/health` from us_east, us_west, eu_west | Down for 2 min. `/health` also checks PostgreSQL, so a database outage counts as down |
| Uptime: latency | same check | Above 1 s for 5 min |
| Uptime: certificate | same check | Expires within 14 days, i.e. renewal has stopped working |

Create the policies with `doctl monitoring alert create --tags edupulse ...` and the check with `doctl monitoring uptime create` / `uptime alert create`; `doctl monitoring alert list` shows what's configured. Application metrics (checks per minute, failure rate, open incidents, queue lag) come from the API's `/api/metrics` and are separate.

## Teardown

```bash
docker swarm leave --force   # on each node, workers first
```

Then destroy the Droplets in the console. **Do not** destroy the Volume unless the data is no longer needed: detach it, and it can be attached to a new manager (create `/mnt/edupulse_pgdata` there and label that node `db=true`).
