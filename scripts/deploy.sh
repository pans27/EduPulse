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
set -uo pipefail
tag=$1 deadline=$((SECONDS + $2))
while (( SECONDS < deadline )); do
  # A failed docker command leaves $tasks empty, which never matches Complete.
  tasks=$(docker service ps edupulse_migrate --format '{{.Image}} {{.CurrentState}}') || tasks=
  task=$(grep -m1 ":$tag " <<<"$tasks") || task=
  case $task in *Complete*) echo "migrate: $task"; exit 0 ;; esac
  sleep 3
done
echo "migrate did not complete for $tag. Latest tasks and logs:" >&2
docker service ps --no-trunc --format '{{.Image}} {{.CurrentState}} {{.Error}}' edupulse_migrate | head -5 >&2
docker service logs --tail 30 edupulse_migrate >&2 || true
exit 1
REMOTE

# Success means every service built from this commit runs the new tag, not just that replica counts
# match: counts can be full while old tasks are still serving, or after Swarm rolled an update back.
echo "waiting for the services to run $TAG"
ssh "$MANAGER" bash -s -- "$TAG" "${CONVERGE_WAIT_SECONDS:-180}" "$REGISTRY" <<'REMOTE'
set -uo pipefail
tag=$1 wait=$2 registry=$3 deadline=$((SECONDS + $2))

# Prints why the stack is not ready yet; returns 2 for a state that waiting cannot fix.
not_ready() {
  local listing name replicas spec state running ours=0
  listing=$(docker stack services edupulse --format '{{.Name}} {{.Replicas}}') || { echo "docker stack services failed"; return 1; }
  [[ -n $listing ]] || { echo "docker stack services returned nothing"; return 1; }
  while read -r name replicas; do
    [[ $name == *_migrate ]] && continue  # the job is checked separately
    # Every long-running service at full replicas.
    [[ ${replicas%%/*} == "${replicas#*/}" ]] || { echo "$name at $replicas"; return 1; }
    spec=$(docker service inspect "$name" --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}') \
      || { echo "cannot inspect $name"; return 1; }
    [[ $spec == "$registry/"* ]] || continue  # nginx and postgres are not built by us
    ours=$((ours + 1))
    state=$(docker service inspect "$name" --format '{{if .UpdateStatus}}{{.UpdateStatus.State}}{{end}}') \
      || { echo "cannot inspect $name"; return 1; }
    case $state in
      rollback_*|paused) echo "$name update is $state (spec now $spec)"; return 2 ;;
    esac
    # The spec must point at this tag; after a rollback it points back at the old one.
    [[ $spec == *":$tag@"* || $spec == *":$tag" ]] || { echo "$name spec is $spec"; return 2; }
    running=$(docker service ps "$name" --filter desired-state=running --format '{{.Image}} {{.CurrentState}}') \
      || { echo "cannot list $name tasks"; return 1; }
    [[ -n $running ]] || { echo "$name has no tasks"; return 1; }
    # Each task that should be running must run the new image and actually be Running.
    if grep -v ":$tag Running" <<<"$running" | grep -q .; then
      echo "$name still has: $(grep -v ":$tag Running" <<<"$running" | head -1)"; return 1
    fi
  done <<<"$listing"
  (( ours > 0 )) || { echo "no services built from $registry found"; return 1; }
  return 0
}

while true; do
  reason=$(not_ready); code=$?
  (( code == 0 )) && break
  if (( code == 2 )); then echo "rollout failed: $reason" >&2; exit 1; fi
  if (( SECONDS >= deadline )); then
    echo "services not running $tag after $wait s: $reason" >&2
    docker stack services edupulse --format '{{.Name}} {{.Replicas}} {{.Image}}' >&2
    exit 1
  fi
  sleep 3
done
docker stack services edupulse --format '{{.Name}} {{.Replicas}} {{.Image}}'
REMOTE
curl -fsS -m 10 https://138-197-173-188.sslip.io/health && echo
