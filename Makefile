.PHONY: up down logs test lint fmt build

COMPOSE = docker compose -f infra/docker-compose.yml
APP_NAME = kbeauty-autocommerce

# ── Infrastructure ────────────────────────────────────────────────────────────

up:
	$(COMPOSE) up -d --build

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

# ── Tests (run locally, not inside Docker) ───────────────────────────────────

test:
	pytest -v --tb=short

test-cov:
	pytest -v --tb=short --cov=app --cov-report=term-missing

# ── Code quality ─────────────────────────────────────────────────────────────

lint:
	ruff check app tests

fmt:
	ruff format app tests

# ── DB migrations (inside running api container) ─────────────────────────────

migrate:
	$(COMPOSE) exec api alembic upgrade head

# ── Help ─────────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "  make up          – Start all services in detached mode"
	@echo "  make down        – Stop and remove containers + volumes"
	@echo "  make logs        – Tail all service logs"
	@echo "  make build       – Rebuild Docker images"
	@echo "  make restart     – Restart api + worker"
	@echo "  make ps          – Show running containers"
	@echo "  make test        – Run pytest locally"
	@echo "  make test-cov    – Run pytest with coverage"
	@echo "  make lint        – Lint with ruff"
	@echo "  make fmt         – Format with ruff"
	@echo "  make migrate     – Run alembic migrations inside api container"
	@echo ""
