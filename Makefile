.PHONY: up down logs test test-fast test-last test-cov lint fmt build help

COMPOSE  = docker compose -f infra/docker-compose.yml
APP_NAME = kbeauty-autocommerce
PYTHON   = .venv/bin/python
PYTEST   = .venv/bin/pytest
RUFF     = .venv/bin/ruff

# ── Infrastructure ────────────────────────────────────────────────────────────
# Docker가 없는 환경에서는 'make up-local' 을 사용하세요.

up:
	$(COMPOSE) up -d --build

up-local:
	@echo "[make up-local] Starting PostgreSQL, Redis, API and Worker locally..."
	@sudo pg_ctlcluster 15 main start 2>/dev/null || true
	@sudo redis-server --daemonize yes --logfile /tmp/redis.log 2>/dev/null || true
	@sleep 1
	@sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='kbeauty'" | grep -q 1 \
		|| sudo -u postgres psql -c "CREATE USER kbeauty WITH PASSWORD 'kbeauty';"
	@sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='kbeauty'" | grep -q 1 \
		|| sudo -u postgres psql -c "CREATE DATABASE kbeauty OWNER kbeauty;"
	@pm2 delete kbeauty-api kbeauty-worker 2>/dev/null || true
	pm2 start ecosystem.config.cjs
	@sleep 2
	@curl -sf http://localhost:8000/health && echo " ✓ API healthy" || echo " ✗ API not ready"

down:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f

build:
	$(COMPOSE) build

restart:
	$(COMPOSE) restart api worker

ps:
	$(COMPOSE) ps

# ── Tests ─────────────────────────────────────────────────────────────────────

## Full test suite — mirrors what GitHub Actions CI runs.
test:
	$(PYTEST) -q --tb=short -p no:timeout

## Fast / unit-only pass: skip integration (DB/Redis) and slow (Playwright) tests.
## Fails immediately on the first failure (--maxfail=1).
## Ideal for rapid feedback during development.
test-fast:
	$(PYTEST) -q --tb=short --maxfail=1 -p no:timeout \
		-m "not integration and not slow"

## Re-run only the tests that failed in the last pytest run.
test-last:
	$(PYTEST) -q --tb=short -p no:timeout --lf

## Full suite with coverage report.
test-cov:
	$(PYTEST) -q --tb=short -p no:timeout \
		--cov=app --cov-report=term-missing --cov-report=html:htmlcov

# ── Code quality ─────────────────────────────────────────────────────────────

lint:
	$(RUFF) check app tests

fmt:
	$(RUFF) format app tests

# ── DB migrations (inside running api container) ─────────────────────────────

migrate:
	$(COMPOSE) exec api alembic upgrade head

# ── Help ─────────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "  ── Infrastructure ──────────────────────────────────────────────"
	@echo "  make up           – Start all services via Docker Compose"
	@echo "  make up-local     – Start services locally (no Docker)"
	@echo "  make down         – Stop and remove containers + volumes"
	@echo "  make logs         – Tail all service logs"
	@echo "  make build        – Rebuild Docker images"
	@echo "  make restart      – Restart api + worker"
	@echo "  make ps           – Show running containers"
	@echo ""
	@echo "  ── Tests ───────────────────────────────────────────────────────"
	@echo "  make test         – Full pytest suite (mirrors CI)"
	@echo "  make test-fast    – Unit/mock-only; skip integration & slow tests"
	@echo "  make test-last    – Re-run last failed tests"
	@echo "  make test-cov     – Full suite + HTML coverage report"
	@echo ""
	@echo "  ── Code quality ────────────────────────────────────────────────"
	@echo "  make lint         – Lint with ruff"
	@echo "  make fmt          – Format with ruff"
	@echo ""
	@echo "  ── DB ──────────────────────────────────────────────────────────"
	@echo "  make migrate      – Run alembic migrations inside api container"
	@echo ""
