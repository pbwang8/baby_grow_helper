# BabyGrowHelper — Phase 0 Makefile
#
# Conventions:
#   - All Python invocations go through `uv run` so we never depend on a
#     globally-activated venv.
#   - DB / port / model are configurable via env vars with sane defaults.
#
# Targets:
#   install   one-shot setup: pull Python 3.11, sync deps
#   db-init   create empty SQLite at $(BGH_DB) with full schema
#   seed      ensure child=yaoyao exists in DB
#   run       launch FastAPI on $(BGH_PORT)
#   test      pytest (real Ollama tests are marked 'integration', skipped here)
#   test-all  pytest including 'integration' (requires Ollama running)
#   lint      ruff + mypy --strict
#   fmt       black + ruff --fix
#   clean     wipe caches + the local DB

PY            ?= uv run
BGH_DB        ?= ./data/babygrow.db
BGH_PORT      ?= 8000
BGH_HOST      ?= 127.0.0.1
BGH_LLM_BACKEND ?= local
BGH_OLLAMA_MODEL ?= qwen2.5:3b-instruct

export BGH_DB
export BGH_LLM_BACKEND
export BGH_OLLAMA_MODEL

.PHONY: install db-init db-migrate db-migrate-postgres family-list seed run test test-all lint fmt clean help \
        web-install web-dev web-build web-lint family-trial-up family-trial-down

help:
	@echo "BabyGrowHelper — Phase 0"
	@echo ""
	@echo "  make install   — pull Python 3.11 + sync deps via uv"
	@echo "  make db-init   — initialize SQLite schema at $(BGH_DB)"
	@echo "  make db-migrate — apply SQLite migrations at $(BGH_DB)"
	@echo "  make db-migrate-postgres — apply Postgres migrations via BGH_DATABASE_URL"
	@echo "  make family-list — list invited families (no secrets)"
	@echo "  make seed      — ensure child=yaoyao exists"
	@echo "  make run       — launch FastAPI on $(BGH_HOST):$(BGH_PORT)"
	@echo "  make test      — unit + snapshot tests (no real Ollama)"
	@echo "  make test-all  — also run integration tests (Ollama required)"
	@echo "  make lint      — ruff + mypy --strict"
	@echo "  make fmt       — black + ruff --fix"
	@echo "  make clean     — wipe caches and local DB"
	@echo "  make family-trial-up — start Docker Compose family trial stack"
	@echo "  make family-trial-down — stop Docker Compose family trial stack"

install:
	uv python install 3.11
	uv sync --extra dev

db-init:
	@mkdir -p $(dir $(BGH_DB))
	$(PY) python -m src.core.db --init

db-migrate:
	$(PY) python -m src.core.migrations --backend sqlite --database $(BGH_DB) --apply

db-migrate-postgres:
	$(PY) python -m src.core.migrations --backend postgres --apply

family-list:
	$(PY) python -m src.scripts.family_admin list

seed: db-init
	$(PY) python -m src.core.seed

run: db-init
	$(PY) uvicorn src.api.main:app --host $(BGH_HOST) --port $(BGH_PORT) --reload

test:
	$(PY) pytest -m "not integration"

test-all:
	$(PY) pytest

lint:
	$(PY) ruff check src tests
	$(PY) mypy

fmt:
	$(PY) black src tests
	$(PY) ruff check --fix src tests

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	rm -rf src/**/__pycache__ tests/**/__pycache__
	rm -f $(BGH_DB) $(BGH_DB)-journal $(BGH_DB)-shm $(BGH_DB)-wal

# ---- Phase 1 frontend (web/) ---------------------------------------------
#
# We default to `npm` because it's bundled with Node and needs no global
# install. If you have pnpm available, override on the command line:
#   make web-install WEB_PM=pnpm

WEB_PM ?= npm
DOCKER ?= $(shell command -v docker 2>/dev/null || printf "/Applications/Docker.app/Contents/Resources/bin/docker")
COMPOSE_ENV_FILE ?= .env

web-install:
	cd web && $(WEB_PM) install

web-dev:
	cd web && $(WEB_PM) run dev

web-build:
	cd web && $(WEB_PM) run build

web-lint:
	cd web && $(WEB_PM) run lint && $(WEB_PM) run typecheck

# ---- Phase 2.5 family trial deploy ---------------------------------------

family-trial-up:
	$(DOCKER) compose --env-file $(COMPOSE_ENV_FILE) -f deploy/docker-compose.family-trial.yml up --build

family-trial-down:
	$(DOCKER) compose --env-file $(COMPOSE_ENV_FILE) -f deploy/docker-compose.family-trial.yml down
