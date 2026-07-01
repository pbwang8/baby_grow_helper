#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 backups/postgres/babygrow-YYYYMMDDTHHMMSSZ.dump" >&2
  exit 2
fi

PROJECT_DIR="${BGH_PROJECT_DIR:-/Users/wangpengbo/Documents/Claude/Projects/baby_grow_helper}"
DOCKER_BIN="${DOCKER_BIN:-/Applications/Docker.app/Contents/Resources/bin/docker}"
COMPOSE_FILE="deploy/docker-compose.family-trial.yml"
ENV_FILE=".env"
BACKUP_FILE="$1"

cd "$PROJECT_DIR"

if [ ! -f "$BACKUP_FILE" ]; then
  echo "Backup file not found: $BACKUP_FILE" >&2
  exit 2
fi

echo "About to restore Postgres from:"
echo "  $BACKUP_FILE"
echo
echo "This will DROP existing public schema data in the running family-trial database."
echo "Type RESTORE to continue:"
read -r confirmation

if [ "$confirmation" != "RESTORE" ]; then
  echo "Restore cancelled."
  exit 1
fi

"$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
  sh -lc 'psql -U "${POSTGRES_USER:-babygrow}" -d "${POSTGRES_DB:-babygrow}" -v ON_ERROR_STOP=1 -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"'

"$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
  sh -lc 'pg_restore -U "${POSTGRES_USER:-babygrow}" -d "${POSTGRES_DB:-babygrow}" --no-owner --no-acl' \
  < "$BACKUP_FILE"

echo "Restore complete."
