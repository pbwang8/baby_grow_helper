#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${BGH_PROJECT_DIR:-/Users/wangpengbo/Documents/Claude/Projects/baby_grow_helper}"
DOCKER_BIN="${DOCKER_BIN:-/Applications/Docker.app/Contents/Resources/bin/docker}"
DAYS="${BGH_FEEDBACK_REMINDER_DAYS:-7}"
REPORT_DIR="$PROJECT_DIR/reports"
REPORT_FILE="$REPORT_DIR/feedback-digest.latest.local.txt"
LOG_FILE="$REPORT_DIR/feedback-reminder.local.log"

notify() {
  local title="$1"
  local message="$2"
  /usr/bin/osascript -e "display notification \"${message}\" with title \"${title}\"" >/dev/null 2>&1 || true
}

mkdir -p "$REPORT_DIR"
cd "$PROJECT_DIR"

if [ ! -x "$DOCKER_BIN" ]; then
  echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") docker not found: $DOCKER_BIN" >> "$LOG_FILE"
  notify "BabyGrowHelper 反馈检查" "Docker 命令不可用，无法检查内测反馈。"
  exit 0
fi

if ! "$DOCKER_BIN" compose --env-file .env -f deploy/docker-compose.family-trial.yml ps api >/dev/null 2>&1; then
  echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") compose service unavailable" >> "$LOG_FILE"
  notify "BabyGrowHelper 反馈检查" "家庭内测服务未运行，无法检查反馈。"
  exit 0
fi

set +e
digest="$("$DOCKER_BIN" compose --env-file .env -f deploy/docker-compose.family-trial.yml exec -T api \
  uv run --no-sync python -m src.scripts.feedback_digest --days "$DAYS" 2>&1)"
status=$?
set -e

printf "%s\n" "$digest" > "$REPORT_FILE"

if [ "$status" -ne 0 ]; then
  echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") digest failed: $status" >> "$LOG_FILE"
  notify "BabyGrowHelper 反馈检查" "反馈摘要脚本运行失败，已写入本地日志。"
  exit 0
fi

count="$(printf "%s\n" "$digest" | awk -F '：' '/反馈数量/ {print $2; exit}' | tr -dc '0-9')"
count="${count:-0}"

echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") feedback_count=$count" >> "$LOG_FILE"

if [ "$count" -gt 0 ]; then
  notify "BabyGrowHelper 反馈检查" "最近 ${DAYS} 天有 ${count} 条内测反馈。摘要已保存到 reports。"
fi
