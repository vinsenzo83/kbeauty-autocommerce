#!/usr/bin/env bash
# scripts/mock_webhooks.sh
# ─────────────────────────────────────────────────────────────────────────────
# Sprint 10 – Mock webhook replay script.
#
# Sequentially POSTs all 6 fixture payloads to each channel endpoint,
# then replays duplicates to verify idempotency.
#
# Usage:
#   ./scripts/mock_webhooks.sh                           # default: localhost:8000
#   BASE_URL=http://172.86.127.238 ./scripts/mock_webhooks.sh   # production VPS
#   BASE_URL=http://172.86.127.238 TOKEN=<jwt> ./scripts/mock_webhooks.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
TOKEN="${TOKEN:-}"
FIXTURES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../fixtures/webhooks" && pwd)"

# ── colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓ $*${NC}"; }
fail() { echo -e "${RED}  ✗ $*${NC}"; }
info() { echo -e "${CYAN}  → $*${NC}"; }
sep()  { echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }

# ── post_fixture(label, url, fixture_file, extra_headers...) ──────────────────
post_fixture() {
    local label="$1"
    local url="$2"
    local fixture="$3"
    shift 3

    if [[ ! -f "$fixture" ]]; then
        fail "Fixture not found: $fixture"
        return 1
    fi

    local curl_args=( -s -o /tmp/wh_resp.json -w "%{http_code}"
        -X POST "$url"
        -H "Content-Type: application/json"
    )

    # Optional auth header
    [[ -n "$TOKEN" ]] && curl_args+=( -H "Authorization: Bearer $TOKEN" )

    # Extra headers passed as remaining args
    for h in "$@"; do
        curl_args+=( -H "$h" )
    done

    curl_args+=( --data-binary "@$fixture" )

    local http_code
    http_code=$(curl "${curl_args[@]}")

    local body
    body=$(cat /tmp/wh_resp.json 2>/dev/null || echo "{}")

    local status
    status=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "?")

    if [[ "$http_code" == "200" ]]; then
        ok "$label  HTTP $http_code  status=$status"
    else
        fail "$label  HTTP $http_code  body=$body"
        return 1
    fi
    echo "$body" > /tmp/wh_last_resp.json
}

# ── Wait for API to be healthy ────────────────────────────────────────────────
wait_for_api() {
    info "Waiting for API at $BASE_URL/health ..."
    local i=0
    while ! curl -sf "$BASE_URL/health" > /dev/null 2>&1; do
        sleep 1
        ((i++))
        if (( i > 15 )); then
            fail "API not reachable after 15 s — aborting"
            exit 1
        fi
    done
    ok "API is healthy"
}

# ─────────────────────────────────────────────────────────────────────────────
sep
echo -e "${YELLOW}  KBeauty AutoCommerce – Sprint 10 Mock Webhook Replay${NC}"
sep
info "BASE_URL = $BASE_URL"
info "FIXTURES  = $FIXTURES_DIR"
sep

wait_for_api

# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${YELLOW}[1/3] SHOPIFY webhooks${NC}"
# ══════════════════════════════════════════════════════════════════════════════
post_fixture "Shopify order.created  (1st)" \
    "$BASE_URL/webhook/shopify" \
    "$FIXTURES_DIR/shopify_order_created.json" \
    "X-Shopify-Topic: orders/create"

post_fixture "Shopify product.updated (1st)" \
    "$BASE_URL/webhook/shopify" \
    "$FIXTURES_DIR/shopify_product_updated.json" \
    "X-Shopify-Topic: products/update"

# ── Idempotency replay ────────────────────────────────────────────────────────
info "Replaying Shopify order (idempotency check) ..."
post_fixture "Shopify order.created  (dup – must be duplicate)" \
    "$BASE_URL/webhook/shopify" \
    "$FIXTURES_DIR/shopify_order_created.json" \
    "X-Shopify-Topic: orders/create"

SHOPIFY_DUP_STATUS=$(python3 -c "import json; d=json.load(open('/tmp/wh_last_resp.json')); print(d.get('status','?'))" 2>/dev/null || echo "?")
if [[ "$SHOPIFY_DUP_STATUS" == "duplicate" ]]; then
    ok "Shopify idempotency OK (status=duplicate)"
else
    fail "Shopify idempotency FAIL — expected 'duplicate', got '$SHOPIFY_DUP_STATUS'"
fi

# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${YELLOW}[2/3] SHOPEE webhooks${NC}"
# ══════════════════════════════════════════════════════════════════════════════
post_fixture "Shopee order.created   (1st)" \
    "$BASE_URL/webhook/shopee" \
    "$FIXTURES_DIR/shopee_order_created.json"

post_fixture "Shopee product.updated  (1st)" \
    "$BASE_URL/webhook/shopee" \
    "$FIXTURES_DIR/shopee_product_updated.json"

info "Replaying Shopee order (idempotency check) ..."
post_fixture "Shopee order.created   (dup – must be duplicate)" \
    "$BASE_URL/webhook/shopee" \
    "$FIXTURES_DIR/shopee_order_created.json"

SHOPEE_DUP_STATUS=$(python3 -c "import json; d=json.load(open('/tmp/wh_last_resp.json')); print(d.get('status','?'))" 2>/dev/null || echo "?")
if [[ "$SHOPEE_DUP_STATUS" == "duplicate" ]]; then
    ok "Shopee idempotency OK (status=duplicate)"
else
    fail "Shopee idempotency FAIL — expected 'duplicate', got '$SHOPEE_DUP_STATUS'"
fi

# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${YELLOW}[3/3] TIKTOK SHOP webhooks${NC}"
# ══════════════════════════════════════════════════════════════════════════════
post_fixture "TikTok order.created   (1st)" \
    "$BASE_URL/webhook/tiktok" \
    "$FIXTURES_DIR/tiktok_order_created.json"

post_fixture "TikTok product.updated  (1st)" \
    "$BASE_URL/webhook/tiktok" \
    "$FIXTURES_DIR/tiktok_product_updated.json"

info "Replaying TikTok order (idempotency check) ..."
post_fixture "TikTok order.created   (dup – must be duplicate)" \
    "$BASE_URL/webhook/tiktok" \
    "$FIXTURES_DIR/tiktok_order_created.json"

TIKTOK_DUP_STATUS=$(python3 -c "import json; d=json.load(open('/tmp/wh_last_resp.json')); print(d.get('status','?'))" 2>/dev/null || echo "?")
if [[ "$TIKTOK_DUP_STATUS" == "duplicate" ]]; then
    ok "TikTok idempotency OK (status=duplicate)"
else
    fail "TikTok idempotency FAIL — expected 'duplicate', got '$TIKTOK_DUP_STATUS'"
fi

# ══════════════════════════════════════════════════════════════════════════════
echo ""
sep
echo -e "${YELLOW}  Verification: Admin API queries${NC}"
sep
# ══════════════════════════════════════════════════════════════════════════════
AUTH_HDR=()
[[ -n "$TOKEN" ]] && AUTH_HDR=( -H "Authorization: Bearer $TOKEN" )

info "Webhook events (last 10):"
curl -sf "${AUTH_HDR[@]}" "$BASE_URL/admin/webhook-events?limit=10" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  total={d[\"total\"]}  statuses={set(i[\"status\"] for i in d[\"items\"])}')" \
    2>/dev/null || echo "  (admin auth required — add TOKEN=<jwt>)"

info "Channel orders (last 10):"
curl -sf "${AUTH_HDR[@]}" "$BASE_URL/admin/channel-orders?limit=10" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  total={d[\"total\"]}  channels={set(i[\"channel\"] for i in d[\"items\"])}')" \
    2>/dev/null || echo "  (admin auth required — add TOKEN=<jwt>)"

info "Channel products (last 10):"
curl -sf "${AUTH_HDR[@]}" "$BASE_URL/admin/channel-products?limit=10" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  total={d[\"total\"]}  skus={[i[\"canonical_sku\"] for i in d[\"items\"]]}')" \
    2>/dev/null || echo "  (admin auth required — add TOKEN=<jwt>)"

sep
echo -e "${GREEN}  Mock replay complete — check outputs above.${NC}"
sep
