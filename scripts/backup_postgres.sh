#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${BGH_PROJECT_DIR:-/Users/wangpengbo/Documents/Claude/Projects/baby_grow_helper}"
DOCKER_BIN="${DOCKER_BIN:-/Applications/Docker.app/Contents/Resources/bin/docker}"
BACKUP_DIR="${BGH_BACKUP_DIR:-$PROJECT_DIR/backups/postgres}"
RETENTION_DAYS="${BGH_BACKUP_RETENTION_DAYS:-30}"
MIN_INTERVAL_HOURS="${BGH_BACKUP_MIN_INTERVAL_HOURS:-20}"
REMOTE_HOOK="${BGH_BACKUP_AFTER_HOOK:-}"
COMPOSE_FILE="deploy/docker-compose.family-trial.yml"
ENV_FILE=".env"
LOG_FILE="$PROJECT_DIR/reports/postgres-backup.local.log"

mkdir -p "$BACKUP_DIR" "$PROJECT_DIR/reports"
cd "$PROJECT_DIR"

timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
tmp_file="$BACKUP_DIR/babygrow-$timestamp.dump.tmp"
backup_file="$BACKUP_DIR/babygrow-$timestamp.dump"

log() {
  echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") $*" >> "$LOG_FILE"
}

notify() {
  local title="$1"
  local message="$2"
  /usr/bin/osascript -e "display notification \"${message}\" with title \"${title}\"" >/dev/null 2>&1 || true
}

mtime_epoch() {
  if stat -f %m "$1" >/dev/null 2>&1; then
    stat -f %m "$1"
  else
    stat -c %Y "$1"
  fi
}

latest_backup="$(find "$BACKUP_DIR" -name 'babygrow-*.dump' -type f -print 2>/dev/null | sort | tail -n 1 || true)"
if [ "${BGH_BACKUP_FORCE:-0}" != "1" ] && [ -n "$latest_backup" ] && [ -f "$latest_backup" ]; then
  now_epoch="$(date +%s)"
  latest_epoch="$(mtime_epoch "$latest_backup")"
  min_interval_seconds=$((MIN_INTERVAL_HOURS * 60 * 60))
  age_seconds=$((now_epoch - latest_epoch))
  if [ "$age_seconds" -lt "$min_interval_seconds" ]; then
    log "SKIP latest_backup=$latest_backup age_seconds=$age_seconds min_interval_hours=$MIN_INTERVAL_HOURS"
    echo "$latest_backup"
    exit 0
  fi
fi

if [ ! -x "$DOCKER_BIN" ]; then
  log "ERROR docker not found: $DOCKER_BIN"
  notify "BabyGrowHelper 备份失败" "Docker 命令不可用，Postgres 未备份。"
  exit 2
fi

if ! "$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps postgres >/dev/null 2>&1; then
  log "ERROR postgres compose service unavailable"
  notify "BabyGrowHelper 备份失败" "Postgres 服务未运行，无法备份。"
  exit 2
fi

set +e
"$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
  sh -lc 'pg_dump -U "${POSTGRES_USER:-babygrow}" -d "${POSTGRES_DB:-babygrow}" -Fc --no-owner --no-acl' \
  > "$tmp_file"
status=$?
set -e

if [ "$status" -ne 0 ] || [ ! -s "$tmp_file" ]; then
  rm -f "$tmp_file"
  log "ERROR pg_dump failed status=$status"
  notify "BabyGrowHelper 备份失败" "pg_dump 失败，已写入本地日志。"
  exit 2
fi

mv "$tmp_file" "$backup_file"
size="$(du -h "$backup_file" | awk '{print $1}')"
log "OK backup=$backup_file size=$size"

find "$BACKUP_DIR" -name 'babygrow-*.dump' -type f -mtime +"$RETENTION_DAYS" -print -delete >> "$LOG_FILE" 2>&1 || true

if [ -n "$REMOTE_HOOK" ]; then
  if [ ! -x "$REMOTE_HOOK" ]; then
    log "ERROR remote_hook_not_executable hook=$REMOTE_HOOK backup=$backup_file"
    notify "BabyGrowHelper 远端备份失败" "本地备份完成，但远端 hook 不可执行。"
    exit 3
  fi

  if "$REMOTE_HOOK" "$backup_file" >> "$LOG_FILE" 2>&1; then
    log "OK remote_hook=$REMOTE_HOOK backup=$backup_file"
  else
    status=$?
    log "ERROR remote_hook_failed status=$status hook=$REMOTE_HOOK backup=$backup_file"
    notify "BabyGrowHelper 远端备份失败" "本地备份完成，但远端同步失败。"
    exit 3
  fi
fi

notify "BabyGrowHelper 备份完成" "Postgres 已备份：$size"

echo "$backup_file"
