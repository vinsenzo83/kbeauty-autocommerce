.PHONY: up down logs test test-fast test-last test-cov lint fmt build help \
        prod-up prod-down prod-logs prod-ps prod-build prod-restart \
        prod-migrate prod-health prod-backup prod-deploy

COMPOSE      = docker compose -f infra/docker-compose.yml
COMPOSE_PROD = docker compose -f infra/docker-compose.prod.yml --env-file .env
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

# ── Production targets (Sprint 9+) ───────────────────────────────────────────

## Start full production stack (postgres, redis, api, worker, beat, dashboard)
prod-up:
	$(COMPOSE_PROD) up -d --build

## Stop production stack (containers only, volumes preserved)
prod-down:
	$(COMPOSE_PROD) down

## Stop production stack AND remove volumes (⚠️ deletes DB data!)
prod-down-volumes:
	$(COMPOSE_PROD) down -v

## Tail production logs for all services
prod-logs:
	$(COMPOSE_PROD) logs -f

## Tail production logs for specific service (e.g. make prod-logs-api)
prod-logs-api:
	docker logs -f kbeauty-api

prod-logs-worker:
	docker logs -f kbeauty-worker

prod-logs-dashboard:
	docker logs -f kbeauty-dashboard

## Show running production containers
prod-ps:
	$(COMPOSE_PROD) ps

## Rebuild production images without cache
prod-build:
	$(COMPOSE_PROD) build --no-cache --parallel

## Restart api + worker (zero-downtime rolling restart)
prod-restart:
	$(COMPOSE_PROD) restart api worker beat dashboard

## Run SQL migrations (one-shot container)
prod-migrate:
	$(COMPOSE_PROD) run --rm migrate

## Run health checks on all services
prod-health:
	bash infra/scripts/healthcheck.sh

## Manual database backup to /opt/apps/backups/
prod-backup:
	bash infra/scripts/backup.sh

## Full fresh deployment (run as root on new server)
prod-deploy:
	sudo bash infra/scripts/deploy.sh

## Pull latest code + rebuild + restart (zero-downtime update)
prod-update:
	git pull --rebase origin main
	$(COMPOSE_PROD) build --no-cache api worker beat dashboard
	$(COMPOSE_PROD) run --rm migrate
	$(COMPOSE_PROD) up -d --no-deps api worker beat dashboard
	bash infra/scripts/healthcheck.sh

# ── Help ─────────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "  ── Infrastructure (dev) ────────────────────────────────────────"
	@echo "  make up              – Start dev stack via Docker Compose"
	@echo "  make up-local        – Start services locally (no Docker)"
	@echo "  make down            – Stop dev containers + volumes"
	@echo "  make logs            – Tail all dev logs"
	@echo "  make build           – Rebuild dev images"
	@echo "  make restart         – Restart api + worker"
	@echo "  make ps              – Show running containers"
	@echo ""
	@echo "  ── Production ──────────────────────────────────────────────────"
	@echo "  make prod-deploy     – Full fresh server deployment (run as root)"
	@echo "  make prod-up         – Start production stack"
	@echo "  make prod-down       – Stop production stack (keep volumes)"
	@echo "  make prod-build      – Rebuild production images"
	@echo "  make prod-restart    – Restart api + worker + beat + dashboard"
	@echo "  make prod-update     – Pull latest + rebuild + restart"
	@echo "  make prod-migrate    – Run SQL migrations"
	@echo "  make prod-health     – Run health checks"
	@echo "  make prod-backup     – Manual DB backup"
	@echo "  make prod-logs       – Tail all production logs"
	@echo "  make prod-ps         – Show production containers"
	@echo ""
	@echo "  ── Tests ───────────────────────────────────────────────────────"
	@echo "  make test            – Full pytest suite (mirrors CI)"
	@echo "  make test-fast       – Unit/mock-only; skip integration & slow tests"
	@echo "  make test-last       – Re-run last failed tests"
	@echo "  make test-cov        – Full suite + HTML coverage report"
	@echo ""
	@echo "  ── Code quality ────────────────────────────────────────────────"
	@echo "  make lint            – Lint with ruff"
	@echo "  make fmt             – Format with ruff"
	@echo ""
	@echo "  ── DB ──────────────────────────────────────────────────────────"
	@echo "  make migrate         – Run alembic migrations (dev container)"
	@echo "  make prod-migrate    – Run SQL migrations (production)"
	@echo ""
