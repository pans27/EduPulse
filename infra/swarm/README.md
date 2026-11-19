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
| Placement | Postgres on `db=true`, fake site on `fakesite=true`, API and workers on `app=true`, Nginx on the manager |
| Zero-downtime API updates | `parallelism: 1`, `order: start-first`, automatic rollback on failure |
| Self-healing | Swarm restarts failed tasks; the image healthchecks mark unhealthy replicas |
| Real client IPs | Nginx publishes port 80 in host mode (not the routing mesh); uvicorn runs with `--proxy-headers` |
| Config files across nodes | `nginx/default.conf` and `db-env.sh` ship as Swarm configs, not bind mounts. To change one, bump `NGINX_CONF_VERSION` / `DB_ENV_VERSION` and redeploy |

On the manager, once:

```bash
openssl rand -hex 24 | tr -d '\n' | docker secret create pg_password -
openssl rand -hex 24 | tr -d '\n' | docker secret create fake_admin_token -
```

Then, from a checkout of the repo on the manager (images must already be in the registry):

```bash
REGISTRY=registry.digitalocean.com/<registry> TAG=<git-sha> \
  docker stack deploy --with-registry-auth -c infra/swarm/stack.yml edupulse
docker stack services edupulse
```

Postgres only reads `POSTGRES_PASSWORD_FILE` when it initializes an empty data directory. Rotating the secret later also needs `ALTER USER edupulse PASSWORD ...`.

### Tested locally (single-node Swarm on Docker Desktop, 2026-11-19)

| Check | Result |
|---|---|
| All services converge | api 3/3, worker 2/2, postgres, fake site, nginx 1/1 |
| `/health` through Nginx | 200, database ok, with the password only in the secret |
| `docker service inspect` | No password in the service environment |
| Kill one API container | 2/3 → 3/3 within 20 s |
| `docker service update --force edupulse_api` while polling `/health` 4×/s | 200/200 requests succeeded, update completed in about 40 s |

A first version capped API replicas at 2 per node; the start-first update then had no free slot for the new replica and hung. The label constraint replaced the cap.

## Teardown

```bash
docker swarm leave --force   # on each node, workers first
```

Then destroy the Droplets in the console. **Do not** destroy the Volume unless the data is no longer needed.
