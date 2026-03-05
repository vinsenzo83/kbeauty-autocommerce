#!/usr/bin/env bash
# =============================================================================
# infra/scripts/healthcheck.sh
# KBeauty AutoCommerce – Production Health Verification Script
#
# Checks:
#   1. All Docker containers running
#   2. API health endpoint  (http://127.0.0.1:8000/health)
#   3. Dashboard reachable  (http://127.0.0.1:3001)
#   4. Nginx proxy          (http://127.0.0.1/health)
#   5. Celery worker alive  (celery inspect ping)
#   6. PostgreSQL reachable (pg_isready inside container)
#   7. Redis reachable      (redis-cli ping inside container)
#
# Exit codes:
#   0 – all checks passed
#   1 – one or more checks failed
# =============================================================================

set -uo pipefail

APP_DIR="${APP_DIR:-/opt/apps/kbeauty-autocommerce}"
COMPOSE_FILE="$APP_DIR/infra/docker-compose.prod.yml"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
BOLD='\033[1m'; NC='\033[0m'

pass()  { echo -e "  ${GREEN}✓${NC}  $*"; }
fail()  { echo -e "  ${RED}✗${NC}  $*"; FAILED=$((FAILED+1)); }
warn()  { echo -e "  ${YELLOW}!${NC}  $*"; }

FAILED=0
COMPOSE="docker compose -f $COMPOSE_FILE"

echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${BOLD}  KBeauty AutoCommerce – Health Check${NC}"
echo -e "${BOLD}  $(date)${NC}"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""

# ── 1. Docker containers ─────────────────────────────────────────────────────
echo -e "${BOLD}[1] Docker Containers${NC}"

EXPECTED_CONTAINERS=(
    "kbeauty-postgres"
    "kbeauty-redis"
    "kbeauty-api"
    "kbeauty-worker"
    "kbeauty-beat"
    "kbeauty-dashboard"
)

for container in "${EXPECTED_CONTAINERS[@]}"; do
    STATUS=$(docker inspect --format='{{.State.Status}}' "$container" 2>/dev/null || echo "not_found")
    HEALTH=$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}no_healthcheck{{end}}' "$container" 2>/dev/null || echo "unknown")

    if [[ "$STATUS" == "running" ]]; then
        if [[ "$HEALTH" == "healthy" || "$HEALTH" == "no_healthcheck" ]]; then
            pass "$container  (status=$STATUS, health=$HEALTH)"
        else
            warn "$container  (status=$STATUS, health=$HEALTH)"
        fi
    else
        fail "$container  (status=$STATUS)"
    fi
done

# ── 2. API health endpoint ────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[2] API Health Endpoint${NC}"

HTTP_CODE=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 5 "http://127.0.0.1:8000/health" 2>/dev/null || echo "000")
if [[ "$HTTP_CODE" == "200" ]]; then
    BODY=$(curl -sS --max-time 5 "http://127.0.0.1:8000/health" 2>/dev/null || echo "{}")
    pass "http://127.0.0.1:8000/health  → HTTP $HTTP_CODE  |  $BODY"
else
    fail "http://127.0.0.1:8000/health  → HTTP $HTTP_CODE"
fi

# ── 3. Dashboard ──────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[3] Dashboard${NC}"

HTTP_CODE=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 10 "http://127.0.0.1:3001" 2>/dev/null || echo "000")
if [[ "$HTTP_CODE" =~ ^(200|301|302)$ ]]; then
    pass "http://127.0.0.1:3001  → HTTP $HTTP_CODE"
else
    fail "http://127.0.0.1:3001  → HTTP $HTTP_CODE"
fi

# ── 4. Nginx proxy ────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[4] Nginx Reverse Proxy${NC}"

HTTP_CODE=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 5 "http://127.0.0.1/health" 2>/dev/null || echo "000")
if [[ "$HTTP_CODE" == "200" ]]; then
    pass "http://127.0.0.1/health  → HTTP $HTTP_CODE  (Nginx → API proxy OK)"
else
    fail "http://127.0.0.1/health  → HTTP $HTTP_CODE"
fi

HTTP_CODE=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 5 "http://127.0.0.1/" 2>/dev/null || echo "000")
if [[ "$HTTP_CODE" =~ ^(200|301|302)$ ]]; then
    pass "http://127.0.0.1/  → HTTP $HTTP_CODE  (Nginx → Dashboard proxy OK)"
else
    fail "http://127.0.0.1/  → HTTP $HTTP_CODE"
fi

# ── 5. Celery worker ──────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[5] Celery Worker${NC}"

PING=$(docker exec kbeauty-worker \
    celery -A app.workers.celery_app:celery_app inspect ping \
    --timeout 8 2>&1 || echo "ERROR")

if echo "$PING" | grep -q "pong"; then
    pass "Celery worker responding to ping"
else
    fail "Celery worker NOT responding  ($PING)"
fi

# ── 6. PostgreSQL ─────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[6] PostgreSQL (internal)${NC}"

PG_STATUS=$(docker exec kbeauty-postgres \
    pg_isready -U "${POSTGRES_USER:-kbeauty}" 2>&1 || echo "failed")

if echo "$PG_STATUS" | grep -q "accepting connections"; then
    pass "PostgreSQL: $PG_STATUS"
else
    fail "PostgreSQL: $PG_STATUS"
fi

# ── 7. Redis ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[7] Redis (internal)${NC}"

REDIS_PING=$(docker exec kbeauty-redis redis-cli ping 2>&1 || echo "failed")
if [[ "$REDIS_PING" == "PONG" ]]; then
    pass "Redis: PONG"
else
    fail "Redis: $REDIS_PING"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}  ALL CHECKS PASSED${NC}"
else
    echo -e "${RED}${BOLD}  $FAILED CHECK(S) FAILED${NC}"
fi
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""

exit $FAILED
