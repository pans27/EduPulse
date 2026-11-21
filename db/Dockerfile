# One-shot schema migration image. Build from the repository root:
#   docker build -f db/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY db/requirements.txt db/requirements.txt
RUN pip install --no-cache-dir -r db/requirements.txt
COPY alembic.ini .
COPY db ./db
# env.py reads DATABASE_URL. In Swarm the db-env.sh entrypoint builds it from the pg_password secret.
CMD ["alembic", "upgrade", "head"]
