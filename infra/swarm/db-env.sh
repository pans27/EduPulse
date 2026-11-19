#!/bin/sh
# Entrypoint shim for API, worker and dispatcher containers in Swarm.
# Builds DATABASE_URL from the pg_password Docker secret so the password never
# appears in the stack file, `docker service inspect`, or the image.
set -eu
PASSWORD=$(cat /run/secrets/pg_password)
export DATABASE_URL="postgresql://${POSTGRES_USER}:${PASSWORD}@${POSTGRES_HOST:-postgres}:5432/${POSTGRES_DB}"
exec "$@"
