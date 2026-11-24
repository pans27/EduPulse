#!/usr/bin/env bash
# Drive a live EduPulse demo on the DigitalOcean cluster from a laptop terminal.
#
#   scripts/demo.sh break <resource> [mode] [k=v ...]  # fake-site resource fails (default mode not_found)
#   scripts/demo.sh fix <resource>                     # back to ok
#   scripts/demo.sh modes                              # fake-site resources and their current modes
#   scripts/demo.sh status                             # resource states and open incidents
#   scripts/demo.sh watch                              # status, refreshed every 2 s (Ctrl-C to stop)
#   scripts/demo.sh mail                               # alert emails received by the mock mail sink
#   scripts/demo.sh mail-reset                         # empty the mail sink before a demo
#   scripts/demo.sh services                           # Swarm services: replicas, version, nodes
#   scripts/demo.sh kill-api                           # kill one API container; Swarm replaces it
#   scripts/demo.sh ws [minutes]                       # open dashboard WebSockets per API replica (default 120 min of logs)
#   scripts/demo.sh restart-db                         # replace the Postgres container; show the data survived
#   scripts/demo.sh logs [service] [minutes]           # recent JSON logs, readable (default: worker, 2 min)
#   scripts/demo.sh db                                 # open Adminer (database viewer) via an SSH tunnel
#   scripts/demo.sh remove <resource> [course]         # delete a registered resource (name or …/r/<resource>)
#                                                      # (rehearsal reset; the API has no delete yet)
#
# Modes: ok not_found server_error slow flapping fail_every_k login_redirect unauthorized head_not_allowed
# Fields: delay_ms=<ms> (slow), fail_rate=<0..1> (flapping), k=<n> (fail_every_k)
# Fake-site resources that exist: lecture5, a1-handout, dataset (any other name answers 404).
# Needs SSH access as root to the Droplets. The fake-site admin token and the database password stay
# on the servers: commands run inside the containers that already have them.
set -euo pipefail

usage() { sed -n '3,/^set -euo/p' "$0" | sed '$d'; exit 1; }

MANAGER=${MANAGER:-138.197.173.188}
# Public IP of a node, by Swarm hostname (a case, since macOS ships bash 3.2 without associative arrays).
node_ip() {
  case $1 in
    edupulse-manager) echo "$MANAGER" ;;
    edupulse-node-1) echo "${NODE1:-167.99.189.58}" ;;
    edupulse-node-2) echo "${NODE2:-143.110.222.139}" ;;
  esac
}
SITE=https://138-197-173-188.sslip.io

on_manager() { ssh -o LogLevel=ERROR "root@$MANAGER" "$@"; }
# Same, but never reads this script's stdin (callers pass heredocs meant for a later ssh).
on_manager_n() { ssh -n -o LogLevel=ERROR "root@$MANAGER" "$@"; }

# Run a command on whichever node hosts a running task of the given service.
on_service_node() {
  local service=$1; shift
  local node ip
  node=$(on_manager_n "docker service ps edupulse_$service --filter desired-state=running --format '{{.Node}}' | head -1")
  ip=$(node_ip "$node")
  [[ -n $ip ]] || { echo "no running task for edupulse_$service (node '$node')" >&2; exit 1; }
  ssh -o LogLevel=ERROR "root@$ip" "$@"
}

psql_manager() { on_manager 'docker exec -i $(docker ps -q -f name=edupulse_postgres) psql -U edupulse -d edupulse -Atq -F " | "'; }

# PUT a behaviour on the fake site from inside its container, where the token is mounted.
fake_put() {
  local name=$1 body=$2
  on_service_node fake-site "docker exec -i \$(docker ps -q -f name=edupulse_fake-site) python -" <<PY
import json, urllib.request
token = open("/run/secrets/fake_admin_token").read().strip()
req = urllib.request.Request("http://localhost:8000/admin/resources/$name", method="PUT", data=b'$body',
                             headers={"x-admin-token": token, "Content-Type": "application/json"})
print("fake-site:", json.loads(urllib.request.urlopen(req).read())["$name"])
PY
}

sink() { on_manager_n "docker run --rm --network edupulse_backend curlimages/curl:8.11.1 -s $*"; }

status() {
  psql_manager <<'SQL'
\echo '== resources (course | name | state | failures | next check in)'
SELECT course_id, name, coalesce(state, 'awaiting first check'), consecutive_failures,
       CASE WHEN next_check_at > now() THEN to_char(next_check_at - now(), 'MI:SS') ELSE 'due' END
FROM resources ORDER BY course_id, name;
\echo '== open incidents (resource | state | detail | opened)'
SELECT r.name, i.state, i.description, to_char(i.opened_at, 'HH24:MI:SS')
FROM incidents i JOIN resources r ON r.id = i.resource_id WHERE i.resolved_at IS NULL ORDER BY i.opened_at;
\echo '== email outbox (kind | status | count)'
SELECT kind, status, count(*) FROM notification_outbox GROUP BY kind, status ORDER BY kind, status;
SQL
}

case "${1:-}" in
  break)
    [[ $# -ge 2 ]] || usage
    name=$2 mode=${3:-not_found}; shift $(( $# >= 3 ? 3 : 2 ))
    body="{\"mode\":\"$mode\""
    for kv in "$@"; do
      key=${kv%%=*} value=${kv#*=}
      [[ $key =~ ^(delay_ms|fail_rate|k)$ && $value =~ ^[0-9.]+$ ]] || { echo "bad field: $kv" >&2; exit 1; }
      body+=",\"$key\":$value"
    done
    fake_put "$name" "$body}" ;;
  fix)
    [[ $# -eq 2 ]] || { echo "usage: $0 fix <resource>" >&2; exit 1; }
    fake_put "$2" '{"mode":"ok"}' ;;
  modes)
    on_service_node fake-site "docker exec -i \$(docker ps -q -f name=edupulse_fake-site) python -" <<'PY'
import json, urllib.request
token = open("/run/secrets/fake_admin_token").read().strip()
req = urllib.request.Request("http://localhost:8000/admin/resources", headers={"x-admin-token": token})
for name, b in json.loads(urllib.request.urlopen(req).read()).items():
    print(f"{name:12} {b['mode']:16} hits={b['hits']}")
PY
    ;;
  status) status ;;
  watch)
    while true; do clear; date '+%H:%M:%S'; status; sleep 2; done ;;
  mail)
    sink http://mail-sink:8000/messages > "${TMPDIR:-/tmp}/edupulse-mail.json"
    python3 - "${TMPDIR:-/tmp}/edupulse-mail.json" <<'PY'
import json, sys
mails = json.load(open(sys.argv[1]))
print(len(mails), "email(s) received by the mock mail sink")
for m in mails:
    print()
    print("  To:        ", m["to"])
    print("  Subject:   ", m["subject"])
    print("  Message-ID:", m["message_id"])
    for line in m["text"].splitlines():
        print("     ", line)
PY
    echo; sink http://mail-sink:8000/stats; echo ;;
  mail-reset) sink -X POST http://mail-sink:8000/admin/reset; echo ;;
  services)
    on_manager_n 'docker stack services edupulse --format "{{.Name}}|{{.Replicas}}|{{.Image}}" | sed "s#registry.digitalocean.com/edupulse/##" | awk -F"|" "{printf \"%-22s %-22s %s\n\", \$1, \$2, \$3}"
      echo; for s in api worker dispatcher; do printf "%-11s " $s; docker service ps edupulse_$s --filter desired-state=running --format "{{.Node}}" | sort | uniq -c | tr -s " \n" " "; echo; done' ;;
  kill-api)
    on_service_node api 'c=$(docker ps -q -f name=edupulse_api | head -1); docker kill "$c" >/dev/null && echo "killed API container ${c:0:12} on $(hostname) at $(date +%T)"'
    # Watch the replica count fall and recover while the public site keeps answering.
    seen_down=0
    for i in $(seq 1 40); do
      replicas=$(on_manager_n 'docker service ls --filter name=edupulse_api --format "{{.Replicas}}"')
      echo "  $(date +%T)  api replicas: $replicas   site: $(curl -s -o /dev/null -w '%{http_code}' -m 3 $SITE/health)"
      [[ $replicas == 3/3 ]] || seen_down=1
      (( seen_down && i > 1 )) && [[ $replicas == 3/3 ]] && { echo "  back to 3/3"; break; }
      sleep 2
    done ;;
  ws)
    # Which API replica each dashboard is connected to. Uvicorn logs "connection open" / "connection
    # closed" for every WebSocket and Swarm prefixes each line with its task, so open minus closed per
    # running replica = live connections (connections opened before the window are not counted).
    minutes=${2:-120}
    [[ $minutes =~ ^[0-9]+$ ]] || usage
    on_manager_n "docker service ps edupulse_api -f desired-state=running --no-trunc --format '{{.Name}}.{{.ID}}@{{.Node}}'
      echo ---; docker service logs --since ${minutes}m edupulse_api 2>&1 | grep -E 'connection (open|closed)' || true" \
      | awk '
        $0 == "---" { logs = 1; next }
        !logs { split($0, p, /[.@]/); key = p[1] "." p[2] "." substr(p[3], 1, 12) "@" p[4]
                order[++n] = key; label[key] = sprintf("api.%s on %s", p[2], p[4]); next }
        /connection open/   { open[$1]++ }
        /connection closed/ { if (open[$1] > 0) open[$1]-- }  # a close for a connection opened before the window
        END {
          for (i = 1; i <= n; i++) { k = order[i]; c = open[k] + 0
            printf "  %-22s %d open WebSocket%s\n", label[k], c, (c == 1 ? "" : "s") }
        }' | sort ;;
  db)
    # Adminer listens only on the manager's loopback; the tunnel brings it to this laptop.
    on_manager 'docker rm -f ep-adminer >/dev/null 2>&1; docker pull -q adminer:4.8.1 >/dev/null; docker run -d --rm --name ep-adminer --network edupulse_backend -p 127.0.0.1:8081:8080 adminer:4.8.1 >/dev/null'
    echo "Adminer: http://localhost:8081   (System PostgreSQL, Server postgres, Username edupulse, Database edupulse)"
    echo -n "Password: "; on_manager 'docker exec $(docker ps -q -f name=edupulse_postgres) cat /run/secrets/pg_password'; echo
    echo "Tunnel open; press Ctrl-C to close it (Adminer is stopped too)."
    trap 'on_manager "docker rm -f ep-adminer >/dev/null 2>&1"; echo; echo "Adminer stopped."' EXIT
    ssh -o LogLevel=ERROR -N -L 8081:127.0.0.1:8081 "root@$MANAGER" ;;
  restart-db)
    # Persistence evidence: a brand-new Postgres container comes up on the same DigitalOcean Volume
    # and every row written before the restart is still there.
    snapshot() {
      psql_manager <<SQL
SELECT 'users', count(*) FROM users
UNION ALL SELECT 'resources', count(*) FROM resources
UNION ALL SELECT 'incidents', count(*) FROM incidents
UNION ALL SELECT 'check results before restart', count(*) FROM check_results WHERE checked_at <= '$1'
UNION ALL SELECT 'emails before restart', count(*) FROM notification_outbox WHERE created_at <= '$1'
UNION ALL SELECT 'student reports before restart', count(*) FROM student_reports WHERE created_at <= '$1';
SQL
    }
    container() { on_manager_n 'docker ps -q -f name=edupulse_postgres'; }
    cutoff=$(on_manager_n 'docker exec $(docker ps -q -f name=edupulse_postgres) psql -U edupulse -d edupulse -Atc "SELECT now()"')
    before_id=$(container)
    echo "data directory: $(on_manager_n "docker inspect $before_id --format '{{range .Mounts}}{{.Source}}{{end}}'") (DigitalOcean Volume, mounted at /mnt/edupulse_pgdata)"
    echo "== before: Postgres container $before_id"; snapshot "$cutoff" | tee "${TMPDIR:-/tmp}/edupulse-before.txt"
    echo; echo "replacing the Postgres container at $(date +%T) ..."
    started=$SECONDS
    on_manager_n 'docker service update -d -q --force edupulse_postgres >/dev/null'
    # Poll the public site until a new container answers: /health reports the database outage.
    for i in $(seq 1 60); do
      code=$(curl -s -o /dev/null -w '%{http_code}' -m 3 "$SITE/health")
      now_id=$(container)
      echo "  $(date +%T)  /health $code   postgres container ${now_id:-(none)}"
      [[ $code == 200 && -n $now_id && $now_id != "$before_id" ]] && break
      sleep 2
    done
    echo "database back after $((SECONDS - started)) s"
    echo; echo "== after: Postgres container $(container)"; snapshot "$cutoff" | tee "${TMPDIR:-/tmp}/edupulse-after.txt"
    if diff -q "${TMPDIR:-/tmp}/edupulse-before.txt" "${TMPDIR:-/tmp}/edupulse-after.txt" >/dev/null; then
      echo; echo "every row from before the restart is still there"
    else
      echo; echo "rows differ:"; diff "${TMPDIR:-/tmp}/edupulse-before.txt" "${TMPDIR:-/tmp}/edupulse-after.txt" || true
    fi ;;
  logs)
    service=${2:-worker} minutes=${3:-2}
    [[ $service =~ ^[a-z-]+$ && $minutes =~ ^[0-9]+$ ]] || usage
    # Resource ids in the logs are shown with their names.
    echo "SELECT id, name FROM resources;" | psql_manager > "${TMPDIR:-/tmp}/edupulse-names.txt"
    on_manager_n "docker service logs --raw --since ${minutes}m edupulse_$service 2>&1" | tail -200 \
      | python3 -c '
import json, sys
names = dict(line.strip().split(" | ", 1) for line in open(sys.argv[1]) if " | " in line)
events = []
for line in sys.stdin:
    try:
        e = json.loads(line)
    except ValueError:
        continue  # non-JSON lines (e.g. an old placeholder image) are skipped
    if isinstance(e, dict):
        events.append(e)
events.sort(key=lambda e: e.get("ts", ""))  # replicas log separately; merge them by time
for e in events:
    ts, msg = e.pop("ts", "")[-8:], e.pop("msg", "")
    for k in ("level", "service", "instance"):
        e.pop(k, None)
    if "resource" in e:
        e["resource"] = names.get(e["resource"], e["resource"][:8])
    fields = "  ".join(f"{k}={v}" for k, v in e.items() if v is not None)
    print(f"  {ts}  {msg:14} {fields}")
' "${TMPDIR:-/tmp}/edupulse-names.txt" | tail -25 ;;
  remove)
    [[ $# -ge 2 ]] || { echo "usage: $0 remove <resource> [course]" >&2; exit 1; }
    [[ $2 =~ ^[A-Za-z0-9_.-]+$ && ${3:-ece1779} =~ ^[A-Za-z0-9_-]+$ ]] || { echo "bad name" >&2; exit 1; }
    # Check results, incidents, reports and outbox rows go with it (ON DELETE CASCADE).
    # Matches the display name or the fake-site name at the end of the URL (…/r/<name>).
    echo "DELETE FROM resources WHERE course_id = '${3:-ece1779}' AND (name = '$2' OR url LIKE '%/r/$2') RETURNING 'removed ' || name || ' (' || url || ')';" | psql_manager ;;
  *) usage ;;
esac
