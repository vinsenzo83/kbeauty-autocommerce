#!/usr/bin/env bash
# =============================================================================
# infra/scripts/deploy.sh
# KBeauty AutoCommerce – Full Production Deployment Script
#
# Designed for: Ubuntu 24, VPS 172.86.127.238, user: deploy
#
# Usage:
#   chmod +x infra/scripts/deploy.sh
#   sudo bash infra/scripts/deploy.sh        # Fresh install
#   bash infra/scripts/deploy.sh --update    # Pull & redeploy only
#
# What it does:
#   1. Install system packages (Docker, Nginx, UFW)
#   2. Configure firewall (UFW: SSH, 80, 443 only)
#   3. Set up /opt/apps directory
#   4. Clone / update repository
#   5. Set up .env from .env.production template
#   6. Build and start Docker Compose stack
#   7. Run DB migrations
#   8. Configure Nginx reverse proxy
#   9. Verify all services are healthy
# =============================================================================

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
APP_DIR="/opt/apps/kbeauty-autocommerce"
REPO_URL="https://github.com/vinsenzo83/kbeauty-autocommerce.git"
COMPOSE_FILE="infra/docker-compose.prod.yml"
NGINX_CONF_SRC="infra/nginx/kbeauty.conf"
NGINX_CONF_DST="/etc/nginx/sites-available/kbeauty"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
UPDATE_ONLY="${1:-}"
NON_INTERACTIVE="${NON_INTERACTIVE:-0}"  # set to 1 to skip interactive prompts

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'

log()  { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]  ${NC} $*"; }
err()  { echo -e "${RED}[error] ${NC} $*" >&2; }
step() { echo -e "\n${BLUE}══════════════════════════════════════════════${NC}"; \
         echo -e "${BLUE}  $*${NC}"; \
         echo -e "${BLUE}══════════════════════════════════════════════${NC}"; }

# ── Root check ────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 && "$UPDATE_ONLY" != "--update" ]]; then
    err "Fresh install requires root. Run: sudo bash $0"
    exit 1
fi

# =============================================================================
# STEP 1 – System packages (skip if --update)
# =============================================================================
if [[ "$UPDATE_ONLY" != "--update" ]]; then
    step "Step 1: Installing system packages"

    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get upgrade -y -qq

    apt-get install -y -qq \
        git \
        nginx \
        ufw \
        ca-certificates \
        curl \
        make \
        htop \
        unzip

    # Docker Engine (official repo)
    if ! command -v docker &>/dev/null; then
        log "Installing Docker..."
        curl -fsSL https://get.docker.com | sh
    else
        log "Docker already installed: $(docker --version)"
    fi

    # Docker Compose plugin
    if ! docker compose version &>/dev/null; then
        log "Installing docker-compose-plugin..."
        apt-get install -y -qq docker-compose-plugin
    fi

    systemctl enable --now docker
    log "Docker enabled and started"

    # ==========================================================================
    # STEP 2 – Firewall
    # ==========================================================================
    step "Step 2: Configuring UFW firewall"

    ufw --force reset
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow OpenSSH
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw --force enable

    log "UFW rules:"
    ufw status verbose

    # ==========================================================================
    # STEP 3 – App directory
    # ==========================================================================
    step "Step 3: Creating app directory"

    mkdir -p /opt/apps
    if id "$DEPLOY_USER" &>/dev/null; then
        chown -R "$DEPLOY_USER:$DEPLOY_USER" /opt/apps
        # Add deploy user to docker group
        usermod -aG docker "$DEPLOY_USER" 2>/dev/null || true
        log "deploy user '$DEPLOY_USER' added to docker group"
    else
        warn "User '$DEPLOY_USER' not found; skipping chown"
    fi
fi  # end: not --update

# =============================================================================
# STEP 4 – Clone / Update repository
# =============================================================================
step "Step 4: Cloning / updating repository"

if [[ ! -d "$APP_DIR/.git" ]]; then
    log "Cloning repository..."
    git clone "$REPO_URL" "$APP_DIR"
else
    log "Pulling latest changes..."
    cd "$APP_DIR"
    git fetch --all
    git checkout main
    git pull --rebase origin main
fi

cd "$APP_DIR"
log "Repository at: $(git log --oneline -1)"

# =============================================================================
# STEP 5 – Environment file
# =============================================================================
step "Step 5: Environment setup"

if [[ ! -f "$APP_DIR/.env" ]]; then
    log "Copying .env.production → .env"
    cp "$APP_DIR/.env.production" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    warn "⚠️  IMPORTANT: Edit $APP_DIR/.env and fill in all REQUIRED values!"
    warn "   Then re-run:  bash infra/scripts/deploy.sh --update"
    echo ""
    echo "Required values to set:"
    grep "← REQUIRED" "$APP_DIR/.env.production" | sed 's/#.*//' | head -20
    echo ""
    if [[ "$NON_INTERACTIVE" != "1" ]]; then
        read -rp "Press ENTER to continue after editing .env, or Ctrl+C to abort..."
    else
        warn "NON_INTERACTIVE=1 – skipping prompt. Ensure .env is pre-configured."
    fi
else
    log ".env already exists (skipping copy)"
fi

# Validate critical vars
source "$APP_DIR/.env" 2>/dev/null || true
if [[ "${POSTGRES_PASSWORD:-kbeauty}" == "kbeauty" ]] || \
   [[ "${POSTGRES_PASSWORD:-}" == "CHANGE_ME"* ]]; then
    warn "⚠️  POSTGRES_PASSWORD is still default/placeholder! Change it in .env"
fi
if [[ "${JWT_SECRET:-change-me-in-production}" == "change-me-in-production" ]] || \
   [[ "${JWT_SECRET:-}" == "CHANGE_ME"* ]]; then
    warn "⚠️  JWT_SECRET is still default/placeholder! Change it in .env"
fi

# =============================================================================
# STEP 6 – Build and start Docker Compose stack
# =============================================================================
step "Step 6: Building and starting Docker stack"

cd "$APP_DIR"
COMPOSE="docker compose -f $COMPOSE_FILE --env-file .env"

# Pull base images first (faster builds)
log "Pulling base images..."
$COMPOSE pull postgres redis 2>/dev/null || true

# Build application images
log "Building application images..."
$COMPOSE build --no-cache --parallel

# Start infrastructure first
log "Starting PostgreSQL and Redis..."
$COMPOSE up -d postgres redis

log "Waiting for database to be healthy..."
timeout=60
while ! $COMPOSE exec -T postgres pg_isready -U "${POSTGRES_USER:-kbeauty}" &>/dev/null; do
    sleep 2
    timeout=$((timeout - 2))
    if [[ $timeout -le 0 ]]; then
        err "PostgreSQL did not become healthy in time"
        $COMPOSE logs postgres | tail -20
        exit 1
    fi
done
log "PostgreSQL is healthy ✓"

# Start remaining services
log "Starting all services..."
$COMPOSE up -d

log "Container status:"
$COMPOSE ps

# =============================================================================
# STEP 7 – Database migrations
# =============================================================================
step "Step 7: Running database migrations"

log "Executing SQL migrations..."
$COMPOSE run --rm migrate

log "Migrations complete ✓"

# =============================================================================
# STEP 8 – Nginx configuration
# =============================================================================
step "Step 8: Configuring Nginx"

cp "$APP_DIR/$NGINX_CONF_SRC" "$NGINX_CONF_DST"
chmod 644 "$NGINX_CONF_DST"

# Enable site
ln -sf "$NGINX_CONF_DST" /etc/nginx/sites-enabled/kbeauty

# Remove default site
rm -f /etc/nginx/sites-enabled/default

# Test configuration
nginx -t && log "Nginx config valid ✓" || { err "Nginx config test failed!"; exit 1; }

systemctl reload nginx
log "Nginx reloaded ✓"

# =============================================================================
# STEP 9 – Health verification
# =============================================================================
step "Step 9: Health verification"

log "Waiting for services to stabilise (15 s)..."
sleep 15

# API health
if curl -sf "http://127.0.0.1:8000/health" > /dev/null 2>&1; then
    log "✓  API health endpoint: OK"
else
    warn "✗  API health endpoint not responding (check: docker logs kbeauty-api)"
fi

# Dashboard
if curl -sf "http://127.0.0.1:3001" > /dev/null 2>&1; then
    log "✓  Dashboard: OK"
else
    warn "✗  Dashboard not responding (check: docker logs kbeauty-dashboard)"
fi

# Nginx
if curl -sf "http://127.0.0.1/health" > /dev/null 2>&1; then
    log "✓  Nginx → API proxy: OK"
else
    warn "✗  Nginx proxy not working (check: nginx -t)"
fi

# Celery
WORKER_PING=$($COMPOSE exec -T worker celery -A app.workers.celery_app:celery_app inspect ping --timeout 5 2>&1 || true)
if echo "$WORKER_PING" | grep -q "pong"; then
    log "✓  Celery worker: OK"
else
    warn "✗  Celery worker not responding (check: docker logs kbeauty-worker)"
fi

# =============================================================================
# Done
# =============================================================================
step "Deployment complete!"

echo ""
echo "  Application URL:   http://172.86.127.238"
echo "  API health:        http://172.86.127.238/health"
echo "  Admin API:         http://172.86.127.238/admin/"
echo ""
echo "  Container logs:"
echo "    docker logs -f kbeauty-api"
echo "    docker logs -f kbeauty-worker"
echo "    docker logs -f kbeauty-dashboard"
echo ""
echo "  Full log stream:"
echo "    cd $APP_DIR && docker compose -f $COMPOSE_FILE logs -f"
echo ""
echo "  Next steps:"
echo "    1. Install SSL:  sudo certbot --nginx -d yourdomain.com"
echo "    2. Set up daily backups: crontab -e"
echo "       0 2 * * * /opt/apps/kbeauty-autocommerce/infra/scripts/backup.sh"
echo ""
