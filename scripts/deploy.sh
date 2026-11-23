#!/usr/bin/env bash
# Build, push and deploy the committed HEAD to the DigitalOcean Swarm.
#
#   scripts/deploy.sh            # build + push images for HEAD, then stack deploy
#   scripts/deploy.sh --no-build # redeploy HEAD's stack files with images already in the registry
#
# Needs: doctl logged in, docker buildx, SSH access to the manager as root.
# Images are tagged with the short commit SHA, so a deploy is always traceable to a commit and a
# rollback is `git checkout <old-sha> && scripts/deploy.sh --no-build`.
set -euo pipefail

cd "$(dirname "$0")/.."
MANAGER=${MANAGER:-root@138.197.173.188}
REGISTRY=${REGISTRY:-registry.digitalocean.com/edupulse}
TAG=$(git rev-parse --short HEAD)
BUILD=1
[[ ${1:-} == --no-build ]] && BUILD=0

if [[ -n $(git status --porcelain -- services db infra alembic.ini) ]]; then
  echo "Uncommitted changes in services/, db/ or infra/; commit them so the tag matches the code." >&2
  exit 1
fi

if [[ $BUILD == 1 ]]; then
  doctl registry login --expiry-seconds 3600 >/dev/null
  # The Droplets are amd64; the platform flag matters when building on Apple Silicon.
  build() {
    if docker buildx build --quiet --platform linux/amd64 --provenance=false -t "$REGISTRY/$1:$TAG" --push "${@:2}" >/dev/null; then
      echo "pushed $1:$TAG"
    else
      echo "FAILED to build or push $1:$TAG" >&2
      return 1
    fi
  }
  # Build in parallel, then wait on each job by PID: a bare `wait` always returns 0, so a failed
  # image would otherwise go unnoticed and the stack would be deployed with a tag that does not exist.
  pids=()
  build api services/api & pids+=($!)
  build worker services/worker & pids+=($!)
  build fake-site services/fake-site & pids+=($!)
  build mail-sink services/mail-sink & pids+=($!)
  build migrate -f db/Dockerfile . & pids+=($!)
  failed=0
  for pid in "${pids[@]}"; do wait "$pid" || failed=1; done
  if [[ $failed == 1 ]]; then
    echo "Image build failed; nothing was deployed." >&2
    exit 1
  fi
fi

# Ship the stack files from this commit, then deploy. Nginx, Postgres and the secrets stay as they are.
git archive HEAD infra | ssh "$MANAGER" 'rm -rf /opt/edupulse/infra && mkdir -p /opt/edupulse && tar -x -C /opt/edupulse'
ssh "$MANAGER" "cd /opt/edupulse && REGISTRY=$REGISTRY TAG=$TAG docker stack deploy -d --with-registry-auth -c infra/swarm/stack.yml edupulse >/dev/null"
echo "deployed $TAG; waiting for the migrate job"

# Swarm has no depends_on, so the new API replicas start rolling out while migrate runs (they
# answer 503 until the tables exist). What the script can do is refuse to report success unless
# migrate completed for this tag. 5 minutes covers its 10 retries 5 s apart while Postgres starts.
ssh "$MANAGER" bash -s -- "$TAG" "${MIGRATE_WAIT_SECONDS:-300}" <<'REMOTE'
tag=$1 deadline=$((SECONDS + $2))
while (( SECONDS < deadline )); do
  task=$(docker service ps edupulse_migrate --format '{{.Image}} {{.CurrentState}}' | grep ":$tag " | head -1 || true)
  case $task in *Complete*) echo "migrate: $task"; exit 0 ;; esac
  sleep 3
done
echo "migrate did not complete for $tag. Latest tasks and logs:" >&2
docker service ps --no-trunc --format '{{.Image}} {{.CurrentState}} {{.Error}}' edupulse_migrate | head -5 >&2
docker service logs --tail 30 edupulse_migrate >&2 || true
exit 1
REMOTE

echo "waiting for every service to reach its replica count"
ssh "$MANAGER" bash -s -- "${CONVERGE_WAIT_SECONDS:-180}" <<'REMOTE'
deadline=$((SECONDS + $1))
until docker stack services edupulse --format '{{.Name}} {{.Replicas}}' | grep -v _migrate \
      | awk '{split($2, r, "/"); if (r[1] != r[2]) bad = 1} END {exit bad}'; do
  if (( SECONDS >= deadline )); then
    echo "services did not converge:" >&2
    docker stack services edupulse --format '{{.Name}} {{.Replicas}} {{.Image}}' >&2
    exit 1
  fi
  sleep 3
done
docker stack services edupulse --format '{{.Name}} {{.Replicas}} {{.Image}}'
REMOTE
curl -fsS -m 10 https://138-197-173-188.sslip.io/health && echo
