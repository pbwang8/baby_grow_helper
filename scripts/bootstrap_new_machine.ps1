$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

if (-not $env:UV_CACHE_DIR) {
    $env:UV_CACHE_DIR = Join-Path $env:TEMP "uv-cache"
}
$env:PYTHONUTF8 = "1"

foreach ($name in @(
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
    "NO_PROXY", "no_proxy"
)) {
    Remove-Item "Env:\$name" -ErrorAction SilentlyContinue
}

function Require-Command {
    param([Parameter(Mandatory = $true)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing required command: $Name"
    }
}

function Invoke-Npm {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)

    $npmCmd = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    if ($npmCmd) {
        & $npmCmd.Source @Args
    } else {
        & npm @Args
    }
}

Write-Host "== BabyGrowHelper new-machine bootstrap =="
Write-Host "repo: $Root"
Write-Host ""

Require-Command git
Require-Command uv
Require-Command node
if (-not (Get-Command "npm.cmd" -ErrorAction SilentlyContinue) -and -not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "Missing required command: npm"
}
Require-Command ollama

Write-Host "[1/7] tool versions"
git --version
uv --version
node --version
Invoke-Npm --version
ollama --version
Write-Host ""

Write-Host "[2/7] Python dependencies"
uv sync --frozen --extra dev
Write-Host ""

Write-Host "[3/7] Web dependencies"
Push-Location (Join-Path $Root "web")
try {
    Invoke-Npm install
} finally {
    Pop-Location
}
Write-Host ""

Write-Host "[4/7] Initialize default local DB"
New-Item -ItemType Directory -Force -Path (Join-Path $Root "data") | Out-Null
$env:BGH_DB = Join-Path $Root "data\babygrow.db"
uv run --no-sync python -m src.core.db --init
Write-Host ""

Write-Host "[5/7] Build synthetic demo DB: data/demo_xiaoming.db"
$env:BGH_DB = Join-Path $Root "data\demo_xiaoming.db"
@'
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
'@ | uv run --no-sync python -
Write-Host ""

Write-Host "[6/7] Backend tests (no real Ollama/BGE/cloud integration)"
uv run --no-sync pytest -m "not integration"
Write-Host ""

Write-Host "[7/7] Frontend typecheck"
Push-Location (Join-Path $Root "web")
try {
    Invoke-Npm run typecheck
} finally {
    Pop-Location
}
Write-Host ""

Write-Host "Bootstrap done."
Write-Host ""
Write-Host "Run backend:"
Write-Host "  cd `"$Root`""
Write-Host "  `$env:PYTHONUTF8 = `"1`""
Write-Host "  `$env:UV_CACHE_DIR = `"$env:UV_CACHE_DIR`""
Write-Host "  `$env:BGH_DB = `".\data\demo_xiaoming.db`""
Write-Host "  uv run --no-sync uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload"
Write-Host ""
Write-Host "Run frontend:"
Write-Host "  cd `"$Root\web`""
Write-Host "  npm run dev"
Write-Host ""
Write-Host "Open:"
Write-Host "  http://localhost:3000"
