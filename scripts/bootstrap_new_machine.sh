#!/usr/bin/env bash
set -euo pipefail

ROOT_POSIX="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$ROOT_POSIX"
if command -v cygpath >/dev/null 2>&1; then
  ROOT_NATIVE="$(cygpath -w "$ROOT_POSIX")"
else
  ROOT_NATIVE="$ROOT_POSIX"
fi
cd "$ROOT_POSIX"

export UV_CACHE_DIR="${UV_CACHE_DIR:-${TMPDIR:-/tmp}/uv-cache}"
export PYTHONUTF8=1
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy NO_PROXY no_proxy || true

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR: missing required command: $1" >&2
    echo "Install it first, then rerun this script." >&2
    exit 2
  fi
}

echo "== BabyGrowHelper new-machine bootstrap =="
echo "repo: $ROOT"
echo

need git
need uv
need node
need npm
need ollama

echo "[1/7] tool versions"
git --version
uv --version
node --version
npm --version
ollama --version || true
echo

echo "[2/7] Python dependencies"
uv sync --frozen --extra dev
echo

echo "[3/7] Web dependencies"
cd "$ROOT/web"
npm install
cd "$ROOT"
echo

echo "[4/7] Initialize default local DB"
mkdir -p "$ROOT/data"
BGH_DB="$ROOT_NATIVE/data/babygrow.db" uv run --no-sync python -m src.core.db --init
echo

echo "[5/7] Build synthetic demo DB: data/demo_xiaoming.db"
BGH_DB="$ROOT_NATIVE/data/demo_xiaoming.db" uv run --no-sync python - <<'PY'
from pathlib import Path

from src.core import db
from src.scripts.backfill import insert_records, parse_jsonl

db.init_db()
conn = db.get_conn()
try:
    conn.execute(
        "INSERT OR IGNORE INTO children(id, name, birthday) VALUES (?, ?, ?)",
        ("xiaoming", "小明", "2023-06-01"),
    )
    existing = conn.execute(
        "SELECT COUNT(*) FROM events WHERE child_id = ?", ("xiaoming",)
    ).fetchone()[0]
    if existing == 0:
        records = parse_jsonl(Path("tests/fixtures/backfill_xiaoming.jsonl"))
        insert_records(conn, "xiaoming", records)
        print(f"inserted {len(records)} synthetic events")
    else:
        print(f"demo already has {existing} events; skipped backfill")
finally:
    conn.close()
PY
echo

echo "[6/7] Backend tests (no real Ollama/BGE/cloud integration)"
uv run --no-sync pytest -m "not integration"
echo

echo "[7/7] Frontend typecheck"
cd "$ROOT/web"
npm run typecheck
cd "$ROOT"
echo

cat <<EOF
Bootstrap done.

Run backend:
  cd "$ROOT_POSIX"
  export PYTHONUTF8=1
  export UV_CACHE_DIR="\${TMPDIR:-/tmp}/uv-cache"
  export BGH_DB="./data/demo_xiaoming.db"
  uv run --no-sync uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload

Run frontend:
  cd "$ROOT_POSIX/web"
  npm run dev

Open:
  http://localhost:3000
EOF
