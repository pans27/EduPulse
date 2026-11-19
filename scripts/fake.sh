#!/usr/bin/env bash
# Control the fake course site during demos and experiments.
#
#   scripts/fake.sh list                               # every resource, its mode and hit count
#   scripts/fake.sh set lecture5 not_found             # switch a resource to a mode
#   scripts/fake.sh set lecture5 slow delay_ms=5000    # extra fields: delay_ms, fail_rate, k
#   scripts/fake.sh ok lecture5                        # back to healthy
#   scripts/fake.sh load-stats | load-reset            # /load throughput for the load test
#
# Modes: ok not_found server_error slow flapping fail_every_k login_redirect unauthorized head_not_allowed
# FAKE_URL defaults to the local Compose port; FAKE_ADMIN_TOKEN defaults to the value in .env.
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ -z "${FAKE_ADMIN_TOKEN:-}" && -f .env ]]; then
  FAKE_ADMIN_TOKEN=$(grep -E '^FAKE_ADMIN_TOKEN=' .env | cut -d= -f2-)
fi
URL=${FAKE_URL:-http://localhost:8001}
TOKEN=${FAKE_ADMIN_TOKEN:-dev-token}

call() { curl -sS --fail-with-body -H "x-admin-token: $TOKEN" "$@"; echo; }
usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

case "${1:-}" in
  list)       call "$URL/admin/resources" ;;
  set)
    [[ $# -ge 3 ]] || usage
    name=$2 mode=$3; shift 3
    body="\"mode\":\"$mode\""
    for kv in "$@"; do
      key=${kv%%=*} value=${kv#*=}
      [[ $key =~ ^(delay_ms|fail_rate|k)$ && $value =~ ^[0-9.]+$ ]] || { echo "bad field: $kv" >&2; exit 1; }
      body+=",\"$key\":$value"
    done
    call -X PUT -H 'Content-Type: application/json' -d "{$body}" "$URL/admin/resources/$name" ;;
  ok)         [[ $# -eq 2 ]] || usage; "$0" set "$2" ok ;;
  load-stats) call "$URL/admin/load-stats" ;;
  load-reset) call -X POST "$URL/admin/load-stats/reset" ;;
  *)          usage ;;
esac
