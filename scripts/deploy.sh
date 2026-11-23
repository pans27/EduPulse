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
  build() { docker buildx build --quiet --platform linux/amd64 --provenance=false -t "$REGISTRY/$1:$TAG" --push "${@:2}" >/dev/null && echo "pushed $1:$TAG"; }
  build api services/api &
  build worker services/worker &
  build fake-site services/fake-site &
  build mail-sink services/mail-sink &
  build migrate -f db/Dockerfile . &
  wait
fi

# Ship the stack files from this commit, then deploy. Nginx, Postgres and the secrets stay as they are.
git archive HEAD infra | ssh "$MANAGER" 'rm -rf /opt/edupulse/infra && mkdir -p /opt/edupulse && tar -x -C /opt/edupulse'
ssh "$MANAGER" "cd /opt/edupulse && REGISTRY=$REGISTRY TAG=$TAG docker stack deploy -d --with-registry-auth -c infra/swarm/stack.yml edupulse >/dev/null"
echo "deployed $TAG; waiting for the migrate job and services"

ssh "$MANAGER" "
  for i in \$(seq 1 60); do
    state=\$(docker service ps edupulse_migrate --format '{{.Image}} {{.CurrentState}}' | grep ':$TAG' | head -1)
    case \$state in *Complete*) break ;; esac
    sleep 3
  done
  echo \"migrate: \${state:-not finished}\"
  for i in \$(seq 1 40); do
    docker stack services edupulse --format '{{.Name}} {{.Replicas}}' | grep -v migrate | awk '{split(\$2, r, \"/\"); if (r[1] != r[2]) bad = 1} END {exit bad}' && break
    sleep 3
  done
  docker stack services edupulse --format '{{.Name}} {{.Replicas}} {{.Image}}'
"
curl -fsS -m 10 https://138-197-173-188.sslip.io/health && echo
