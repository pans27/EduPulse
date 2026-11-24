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
    [[ $# -ge 2 ]] || { sed -n '3,19p' "$0"; exit 1; }
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
  db)
    # Adminer listens only on the manager's loopback; the tunnel brings it to this laptop.
    on_manager 'docker rm -f ep-adminer >/dev/null 2>&1; docker pull -q adminer:4.8.1 >/dev/null; docker run -d --rm --name ep-adminer --network edupulse_backend -p 127.0.0.1:8081:8080 adminer:4.8.1 >/dev/null'
    echo "Adminer: http://localhost:8081   (System PostgreSQL, Server postgres, Username edupulse, Database edupulse)"
    echo -n "Password: "; on_manager 'docker exec $(docker ps -q -f name=edupulse_postgres) cat /run/secrets/pg_password'; echo
    echo "Tunnel open; press Ctrl-C to close it (Adminer is stopped too)."
    trap 'on_manager "docker rm -f ep-adminer >/dev/null 2>&1"; echo; echo "Adminer stopped."' EXIT
    ssh -o LogLevel=ERROR -N -L 8081:127.0.0.1:8081 "root@$MANAGER" ;;
  remove)
    [[ $# -ge 2 ]] || { echo "usage: $0 remove <resource> [course]" >&2; exit 1; }
    [[ $2 =~ ^[A-Za-z0-9_.-]+$ && ${3:-ece1779} =~ ^[A-Za-z0-9_-]+$ ]] || { echo "bad name" >&2; exit 1; }
    # Check results, incidents, reports and outbox rows go with it (ON DELETE CASCADE).
    # Matches the display name or the fake-site name at the end of the URL (…/r/<name>).
    echo "DELETE FROM resources WHERE course_id = '${3:-ece1779}' AND (name = '$2' OR url LIKE '%/r/$2') RETURNING 'removed ' || name || ' (' || url || ')';" | psql_manager ;;
  *) sed -n '3,19p' "$0"; exit 1 ;;
esac
