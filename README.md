# kbeauty-autocommerce

K-Beauty 주문 자동화 백엔드 — FastAPI · PostgreSQL · Redis · Celery

[![Tests](https://github.com/vinsenzo83/kbeauty-autocommerce/actions/workflows/tests.yml/badge.svg)](https://github.com/vinsenzo83/kbeauty-autocommerce/actions/workflows/tests.yml)

| 스프린트 | 상태 | 내용 |
|---|---|---|
| Sprint 1 (v0.1.0) | ✅ 완료 | Shopify 웹훅 수신, HMAC 검증, 중복 방지, 정책 검증 |
| Sprint 2 (v0.2.0) | ✅ 완료 | StyleKorean 공급사 주문 배치, PLACING→PLACED 상태 추가, Admin retry API |
| Sprint 3 (v0.3.0) | ✅ 완료 | My Orders 트래킹 스크래핑, SHIPPED 상태, Celery beat 자동 폴링, Shopify fulfillment |
| Sprint 4 (v0.4.0) | ✅ 완료 | 베스트셀러 500개 크롤링, 상품 DB 저장, Shopify 상품 동기화, 이미지 다운로드 |
| Sprint 5 (v0.5.0) | ✅ 완료 | Admin Dashboard API (JWT 인증, KPI/Alerts/Orders/Tickets/Health) |

---

## CI / 테스트 정책

### GitHub Actions CI

모든 `push` 및 `pull_request`에서 자동으로 전체 pytest suite가 실행됩니다.

```
.github/workflows/tests.yml
 ├─ Runner     : ubuntu-latest
 ├─ Python     : 3.11
 ├─ Services   : postgres:15 (port 5432) + redis:7 (port 6379)
 ├─ Timeout    : 20분
 ├─ DB 격리    : DATABASE_URL_TEST → 전용 CI DB (프로덕션 DB 접근 없음)
 ├─ Command    : pytest -q --tb=short -p no:timeout
 └─ Artifacts  : junit.xml, coverage.xml (14일 보존)
```

#### CI 환경 변수 (자동 설정)

| 변수 | 값 | 설명 |
|------|-----|------|
| `DATABASE_URL_TEST` | `postgresql+asyncpg://kbeauty:kbeauty@localhost:5432/kbeauty_test` | CI 전용 DB — 프로덕션 절대 불접 |
| `REDIS_URL` | `redis://localhost:6379/0` | CI Redis 서비스 |
| `SHOPIFY_WEBHOOK_SECRET` | `test_secret` | HMAC 단위 테스트용 |
| `STORAGE_PATH` | `./storage` | 임시 아티팩트 저장 경로 |
| `JWT_SECRET` | `ci-test-jwt-secret` | JWT 인증 테스트용 |

> **릴리즈(tag) 규칙**: CI가 green 상태일 때만 `git tag vX.Y.Z`를 생성하고 GitHub Release를 만듭니다.
> 빨간 CI로는 릴리즈하지 않습니다.

### 로컬 개발 테스트 명령어

| 명령어 | 설명 |
|---|---|
| `make test` | 전체 suite 실행 — CI와 동일한 명령 |
| `make test-fast` | 단위/Mock 테스트만 (`-m "not integration and not slow"`, `--maxfail=1`) — 빠른 피드백 |
| `make test-last` | 마지막으로 실패한 테스트만 재실행 (`--lf`) |
| `make test-cov` | 전체 suite + HTML 커버리지 리포트 (`htmlcov/`) |

```bash
# 개발 중 빠른 피드백 루프 (DB/Playwright 없이 실행 가능)
make test-fast

# 특정 테스트만
.venv/bin/pytest tests/test_hmac.py -v

# CI 전 최종 확인 (전체 suite)
make test

# 실패한 테스트만 빠르게 재실행
make test-last

# 커버리지 리포트 (htmlcov/index.html)
make test-cov
```

### pytest 마커

```python
import pytest

@pytest.mark.integration   # 라이브 Postgres / Redis 필요
@pytest.mark.slow          # Playwright 브라우저 or 10초+ 소요
```

`tests/conftest.py`의 `pytest_collection_modifyitems`가 파일명 기반으로 자동 마킹합니다.
수동으로 데코레이터를 붙이지 않아도 됩니다.

| 테스트 파일 | 자동 마커 |
|---|---|
| `test_webhook_idempotency` | `integration` |
| `test_order_state_machine` | `integration` |
| `test_sprint2_supplier` | `integration`, `slow` |
| `test_sprint3_tracking` | `integration`, `slow` |
| `test_sprint4_products` | `slow` |

`integration` / `slow` 마커가 붙은 테스트는 `make test-fast`에서 자동으로 제외됩니다.
CI(`make test`)에서는 모든 마커의 테스트가 실행됩니다.

---

## Sprint 4 — 베스트셀러 크롤러 + Shopify 상품 동기화

### 파이프라인

```
[Celery beat / 12시간마다] crawl_best_sellers()
  └─▶ StyleKorean Best Sellers 페이지 순회 (최대 500개 URL 수집)
        ├─▶ 각 상품 페이지 Playwright 로드 → parse_product_page(html)
        │     └─▶ name, brand, price, sale_price, stock_status, image_urls 파싱
        └─▶ product_service.upsert_product() → products 테이블 upsert

[Celery beat / 30분마다] sync_products_to_shopify()
  └─▶ get_unsynced_products() → shopify_product_id가 NULL인 상품 조회
        ├─▶ ShopifyProductService.create_or_update_product(product)
        │     ├─▶ POST /admin/api/2024-01/products.json  (신규)
        │     └─▶ metafield namespace=supplier, key=product_url 설정
        └─▶ mark_synced(product, shopify_id) → shopify_product_id 저장
```

### 새 파일 구조

```
app/
├── crawlers/
│   ├── product_parser.py       # HTML → dict 순수 파서 (외부 의존성 없음)
│   ├── stylekorean_crawler.py  # Playwright 크롤러 + URL 수집 + upsert 호출
│   └── image_downloader.py     # httpx 비동기 이미지 다운로드
├── models/
│   └── product.py              # Product ORM 모델
├── services/
│   ├── product_service.py      # upsert_product, get_unsynced_products, mark_synced
│   └── shopify_product_service.py  # create_or_update_product + metafield 설정
└── workers/
    └── tasks_products.py       # crawl_best_sellers, sync_products_to_shopify Celery tasks
migrations/
└── 0004_sprint4_products.sql   # products 테이블 + 인덱스 + trigger (idempotent)
```

### 새 환경 변수

```dotenv
STYLEKOREAN_BASE_URL=https://www.stylekorean.com  # 기본 URL (변경 불필요)
PRODUCT_CRAWL_LIMIT=500         # 크롤 최대 상품 수
PRODUCT_CRAWL_INTERVAL=43200    # 크롤 주기 (초, 기본값 43200 = 12시간)
PRODUCT_SYNC_INTERVAL=1800      # Shopify 동기화 주기 (초, 기본값 1800 = 30분)
```

### DB 마이그레이션 (Sprint 4)

```bash
psql $DATABASE_URL -f migrations/0004_sprint4_products.sql
```

### Celery Beat 스케줄 (v0.4.0 전체)

```
poll-tracking-every-interval      → tasks_tracking.poll_tracking        10분마다
crawl-best-sellers-every-12h      → tasks_products.crawl_best_sellers   12시간마다
sync-products-to-shopify-every-30m→ tasks_products.sync_products_to_shopify 30분마다
```

### 수동 크롤 트리거

```python
from app.workers.celery_app import celery_app
celery_app.send_task("workers.tasks_products.crawl_best_sellers")
celery_app.send_task("workers.tasks_products.sync_products_to_shopify")
```

### 이미지 저장 경로

```
{STORAGE_PATH}/product_images/{product_id}/0.jpg
{STORAGE_PATH}/product_images/{product_id}/1.jpg
...
```

---

## Sprint 3 — 트래킹 자동화

### 전체 파이프라인

```
PLACED (supplier_order_id 보유)
  └─▶ [Celery beat / 10분마다] poll_tracking()
        ├─▶ StyleKoreanClient.get_tracking(supplier_order_id)
        │     └─▶ My Orders 페이지 스크래핑 → tracking_number + carrier
        ├─▶ 트래킹 발견 시:
        │     ├─▶ Order → SHIPPED (tracking_number, tracking_url, shipped_at 저장)
        │     └─▶ Shopify fulfillment 생성 (notify_customer=true)
        └─▶ 미발송 시: 조용히 스킵
```

### 새 환경 변수

```dotenv
TRACKING_POLL_INTERVAL=600   # 폴링 주기 (초, 기본값 600 = 10분)
```

### 실패 아티팩트 경로

| 유형 | 저장 경로 |
|---|---|
| 주문 배치 실패 | `{STORAGE_PATH}/bot_failures/{order_id}/` |
| 트래킹 스크래핑 실패 | `{STORAGE_PATH}/bot_failures/tracking/{supplier_order_id}/` |

각 디렉토리에 `screenshot.png`, `page.html`, `reason.txt` 저장됩니다.

### DB 마이그레이션 (Sprint 3)

```bash
psql $DATABASE_URL -f migrations/0003_sprint3_tracking_fields.sql
```

### 지원 택배사 (자동 URL 생성)

| 택배사 | 트래킹 URL |
|---|---|
| DHL | dhl.com |
| FedEx | fedex.com |
| UPS | ups.com |
| USPS | usps.com |
| EMS | ems.com.cn |
| CJ대한통운 | cjlogistics.com |
| ePacket | 17track.net |
| SF Express | sf-express.com |

---

## Sprint 2 — 공급사 자동 발주 모듈

### 아키텍처 추가사항

```
VALIDATED → PLACING → (StyleKoreanClient.create_order) → PLACED
                                                        ↘ FAILED (+ event_log artifact)
```

### 새 환경 변수

```dotenv
STYLEKOREAN_EMAIL=your@email.com
STYLEKOREAN_PASSWORD=your-password
STORAGE_PATH=./storage          # 실패 아티팩트 저장 경로
```

### Playwright 설치 (실제 발주 실행 시)

```bash
pip install playwright
playwright install chromium
```

> **테스트는 Playwright 없이 실행됩니다.** 모든 tests는 mock만 사용합니다.

### 개발 환경에서 수동 태스크 트리거

```python
# Python shell (venv 활성화 후)
from app.workers.celery_app import celery_app
celery_app.send_task("workers.tasks_order.process_new_order", args=["<order_uuid>"])

# 또는 retry-place (FAILED 상태 주문 재시도)
celery_app.send_task("workers.tasks_order.retry_place_order", args=["<order_uuid>"])
```

또는 Admin API 엔드포인트로 재시도:

```bash
curl -X POST http://localhost:8000/admin/orders/<order_uuid>/retry-place
```

### 실패 아티팩트 저장 위치

Playwright 실행 중 오류 발생 시 아래 경로에 저장됩니다:

```
{STORAGE_PATH}/bot_failures/{order_id}/
  ├── screenshot.png   # 오류 발생 시점 스크린샷
  ├── page.html        # 오류 발생 시점 HTML 덤프
  └── reason.txt       # 오류 이유 + 타임스탬프
```

### DB 마이그레이션 (Sprint 2)

기존 PostgreSQL DB가 있는 경우:

```bash
# Docker Compose 환경
docker compose exec api psql $DATABASE_URL -f migrations/0002_sprint2_supplier_fields.sql

# 로컬 환경
psql postgresql://kbeauty:kbeauty@localhost/kbeauty -f migrations/0002_sprint2_supplier_fields.sql
```

새로 시작하는 경우 `create_all`이 자동으로 모든 컬럼을 생성합니다.

### 새 API 엔드포인트

| Method | Path | 설명 |
|---|---|---|
| `POST` | `/admin/orders/{id}/retry-place` | FAILED 주문 공급사 발주 재시도 |

> TODO: 프로덕션 배포 전 JWT 인증 추가 필요 (`app/routers/admin.py` 참고)

---

---

## 아키텍처 개요

```
┌─────────────────────────────────────────────┐
│  Shopify Webhook  POST /webhooks/shopify/    │
│                    order-created             │
└───────────────────────┬─────────────────────┘
                        │ HMAC 검증 + 중복 방지
                        ▼
               ┌────────────────┐
               │   FastAPI API  │  :8000
               └───────┬────────┘
          persist order │ enqueue task
                        ▼
     ┌──────────────────────────────────┐
     │  Celery Worker                   │
     │  process_new_order(order_id)     │
     │  → 정책 검증 → VALIDATED / FAILED│
     └──────────────────────────────────┘
               │              │
         PostgreSQL          Redis
         (orders,          (broker &
         event_logs)        backend)
```

---

## 빠른 시작

### 사전 준비

- Docker & Docker Compose v2
- Python 3.11+ (로컬 테스트 실행 시)

### 1. 환경 변수 설정

```bash
cp .env.sample .env
# .env 파일에서 SHOPIFY_WEBHOOK_SECRET 등 값을 수정하세요.
```

### 2. 전체 스택 실행

```bash
make up
```

서비스가 모두 올라오면 헬스 체크:

```bash
curl http://localhost:8000/health
# {"status":"ok","env":"development"}
```

### 3. 서비스 종료

```bash
make down
```

### 4. 로그 확인

```bash
make logs
```

---

## 테스트 실행

로컬에서 Python 가상환경을 사용합니다. **Docker 없이도 실행 가능합니다** (SQLite in-memory 사용).

```bash
# 가상환경 생성 및 의존성 설치
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install aiosqlite pytest-cov

# 테스트 실행
make test

# 커버리지 포함
make test-cov
```

테스트 항목:
| 파일 | 테스트 내용 |
|------|------------|
| `test_hmac.py` | HMAC 유효 / 무효 / 빈값 / 변조 등 7 케이스 |
| `test_webhook_idempotency.py` | 첫 요청 수락, 중복 요청 무시, 잘못된 서명 거부 |
| `test_order_state_machine.py` | RECEIVED → VALIDATED / FAILED 상태 전이, 정책 검증 규칙 |

---

## 로컬 웹훅 테스트 (curl)

HMAC 서명을 계산하는 Python 스크립트:

```python
#!/usr/bin/env python3
"""scripts/send_webhook.py — 로컬 웹훅 테스트용"""
import base64
import hashlib
import hmac
import json
import subprocess

SECRET = "your-shopify-webhook-secret"  # .env의 SHOPIFY_WEBHOOK_SECRET 값

payload = {
    "id": 112233445566,
    "email": "test@kbeauty.com",
    "total_price": "49000.00",
    "currency": "KRW",
    "financial_status": "paid",
    "shipping_address": {
        "first_name": "지수",
        "address1": "강남구 테헤란로 10",
        "city": "서울",
        "country": "South Korea",
    },
    "line_items": [{"title": "Ceramide Cream 50ml", "quantity": 1, "price": "49000.00"}],
}

raw = json.dumps(payload, separators=(",", ":")).encode()
digest = hmac.new(SECRET.encode(), raw, hashlib.sha256).digest()
signature = base64.b64encode(digest).decode()

print("HMAC:", signature)
print()
print("curl 명령어:")
print(
    f'curl -s -X POST http://localhost:8000/webhooks/shopify/order-created \\\n'
    f'  -H "Content-Type: application/json" \\\n'
    f'  -H "X-Shopify-Hmac-Sha256: {signature}" \\\n'
    f'  -H "X-Shopify-Topic: orders/create" \\\n'
    f'  -d \'{json.dumps(payload, separators=(",", ":"))}\''
)
```

실행:

```bash
python scripts/send_webhook.py
# 출력된 curl 명령어를 그대로 복사해 실행하면 됩니다.
```

또는 직접 한 줄 curl:

```bash
BODY='{"id":112233445566,"email":"test@kbeauty.com","total_price":"49000.00","currency":"KRW","financial_status":"paid","shipping_address":{"address1":"강남구"},"line_items":[]}'
SIG=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "your-shopify-webhook-secret" -binary | base64)

curl -s -X POST http://localhost:8000/webhooks/shopify/order-created \
  -H "Content-Type: application/json" \
  -H "X-Shopify-Hmac-Sha256: $SIG" \
  -H "X-Shopify-Topic: orders/create" \
  -d "$BODY"
```

---

## 프로젝트 구조

```
kbeauty-autocommerce/
├── app/
│   ├── main.py                  # FastAPI 앱 팩토리, 라이프스팸
│   ├── config.py                # pydantic-settings 기반 설정
│   ├── logging.py               # structlog JSON 로깅 설정
│   ├── db/
│   │   └── session.py           # SQLAlchemy 2.0 async 엔진 & 세션
│   ├── models/
│   │   ├── order.py             # Order 모델 (UUID PK, ENUM status)
│   │   └── event_log.py         # EventLog 모델 (중복 방지 해시)
│   ├── services/
│   │   ├── shopify_service.py   # Shopify API 클라이언트 스텁
│   │   ├── order_service.py     # 주문 CRUD + 상태 전이
│   │   └── policy_service.py    # 주문 정책 검증 (paid + shipping)
│   ├── webhooks/
│   │   └── shopify.py           # POST /webhooks/shopify/order-created
│   ├── workers/
│   │   ├── celery_app.py        # Celery 앱 설정 (Asia/Seoul)
│   │   └── tasks_order.py       # process_new_order 태스크
│   └── utils/
│       ├── hmac_verify.py       # Shopify HMAC-SHA256 검증
│       ├── retry.py             # async_retry 데코레이터
│       └── time.py              # KST 타임존 헬퍼
├── infra/
│   ├── Dockerfile               # Python 3.11 멀티 스테이지 이미지
│   └── docker-compose.yml       # api · worker · postgres · redis
├── tests/
│   ├── conftest.py
│   ├── test_hmac.py
│   ├── test_webhook_idempotency.py
│   └── test_order_state_machine.py
├── .env.sample
├── Makefile
├── pytest.ini
├── requirements.txt
└── README.md
```

---

## DB 스키마

### `orders`

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | UUID PK | 내부 식별자 |
| `shopify_order_id` | VARCHAR(64) UNIQUE | Shopify 주문 ID |
| `email` | VARCHAR(255) | 구매자 이메일 |
| `total_price` | NUMERIC(12,2) | 총 결제금액 |
| `currency` | VARCHAR(10) | 통화 (KRW 등) |
| `shipping_address_json` | JSON | 배송지 정보 |
| `line_items_json` | JSON | 주문 상품 목록 |
| `financial_status` | VARCHAR(64) | Shopify 결제 상태 |
| `status` | VARCHAR(16) | RECEIVED / VALIDATED / FAILED |
| `fail_reason` | TEXT | 실패 사유 |
| `created_at` | TIMESTAMPTZ | 생성 시각 |
| `updated_at` | TIMESTAMPTZ | 수정 시각 |

### `event_logs`

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | UUID PK | 내부 식별자 |
| `event_hash` | VARCHAR(64) UNIQUE | SHA-256 중복 방지 해시 |
| `source` | VARCHAR(64) | 이벤트 출처 (shopify, worker 등) |
| `event_type` | VARCHAR(128) | 이벤트 유형 (orders/create 등) |
| `payload_ref` | VARCHAR(128) | shopify_order_id 참조 |
| `note` | TEXT | 비고 (실패 사유 등) |
| `created_at` | TIMESTAMPTZ | 생성 시각 |

---

## Makefile 타겟 요약

```
make up          – 전체 스택 실행 (빌드 포함)
make down        – 컨테이너 + 볼륨 제거
make logs        – 실시간 로그 확인
make build       – Docker 이미지 재빌드
make restart     – api, worker 재시작
make ps          – 실행 중인 컨테이너 목록
make test        – pytest 로컬 실행
make test-cov    – pytest + 커버리지
make lint        – ruff 린트
make fmt         – ruff 포맷
```

---

## 환경 변수 (.env.sample)

```dotenv
APP_ENV=development
DEBUG=false
LOG_LEVEL=INFO

POSTGRES_USER=kbeauty
POSTGRES_PASSWORD=kbeauty
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=kbeauty

REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

SHOPIFY_WEBHOOK_SECRET=your-shopify-webhook-secret
SHOPIFY_API_KEY=
SHOPIFY_API_SECRET=
SHOPIFY_STORE_DOMAIN=your-store.myshopify.com
```

---

## Sprint 7: Multi Supplier Engine

### Overview

Sprint 7 adds multi-supplier support. The system now tracks price and stock for
**StyleKorean**, **Jolse**, and **OliveYoung** side-by-side and automatically
picks the cheapest in-stock supplier for each order.

---

### `supplier_products` Table

One row per `(product_id, supplier)` pair.

| Column               | Type         | Description                                  |
|----------------------|--------------|----------------------------------------------|
| `id`                 | UUID PK      | Auto-generated                               |
| `product_id`         | UUID FK      | References `products.id`                     |
| `supplier`           | TEXT         | `STYLEKOREAN` / `JOLSE` / `OLIVEYOUNG`       |
| `supplier_product_id`| TEXT         | Supplier-side SKU / product ID               |
| `price`              | NUMERIC(12,2)| Latest observed price                        |
| `stock_status`       | TEXT         | `IN_STOCK` / `OUT_OF_STOCK`                  |
| `last_checked_at`    | TIMESTAMPTZ  | When the row was last refreshed              |

Apply migration:

```bash
psql $DATABASE_URL -f migrations/0007_supplier_products.sql
```

---

### Supplier Selection Algorithm

`choose_best_supplier(product_id, session)` in `app/services/supplier_router.py`:

1. Load all `supplier_products` rows for the product.
2. Filter to `IN_STOCK` only.
3. Return the row with the **lowest price**.
4. Tie-breaker: alphabetical supplier name (`JOLSE < OLIVEYOUNG < STYLEKOREAN`).
5. Returns `None` when no IN_STOCK row exists.

---

### Running the Sync Task Manually

```bash
# One-off via Celery CLI (worker must be running):
celery -A app.workers.celery_app call workers.tasks_supplier_products.sync_supplier_products

# One-off directly (useful for debugging):
python -c "
import asyncio
from app.workers.tasks_supplier_products import _run_sync
print(asyncio.run(_run_sync()))
"
```

Celery Beat schedule: **every 60 minutes** (`sync-supplier-products-every-60m`).

---

### New Admin Endpoints

| Method | Path                                       | Description                         |
|--------|--------------------------------------------|-------------------------------------|
| GET    | `/admin/suppliers/products/{product_id}`  | Supplier rows for a product          |
| GET    | `/admin/suppliers/summary`                | Counts by supplier + stock status    |

---

### Testing

```bash
# Fast suite only (mock-only, no DB/network required):
make test-fast
# or:
pytest -q -m "not integration and not slow" --maxfail=1

# Full suite (requires PostgreSQL + Redis):
make test

# Sprint 7 tests only:
pytest tests/test_sprint7_supplier_products.py tests/test_sprint7_supplier_router.py -v
```

**CI is the final gate.** GitHub Actions must be green before a Sprint 7 release.

---

### New Files (Sprint 7)

```
app/crawlers/jolse_inventory.py           – Jolse inventory scraper (Playwright, mockable)
app/crawlers/oliveyoung_inventory.py      – OliveYoung inventory scraper (Playwright, mockable)
app/models/supplier_product.py            – SupplierProduct ORM model
app/services/supplier_product_service.py  – CRUD: upsert / get / get_best_supplier
app/services/supplier_router.py           – choose_best_supplier() + legacy choose_supplier()
app/suppliers/jolse.py                    – JolseClient (SupplierClient stub)
app/suppliers/oliveyoung.py               – OliveYoungClient (SupplierClient stub)
app/workers/tasks_supplier_products.py    – Celery task: sync_supplier_products
migrations/0007_supplier_products.sql     – Idempotent DB migration
tests/test_sprint7_supplier_products.py   – 12 mock-only CRUD tests
tests/test_sprint7_supplier_router.py     – 15 mock-only router + crawler tests
```

---

## Sprint 8: Canonical Layer + Pricing Engine

### Why canonical_products?

The `products` table was StyleKorean-originated (one row per supplier SKU).
This made multi-supplier matching unstable: the same real-world product
had no shared identity across suppliers.

`canonical_products` is the **primary identity** for a real-world product.
It is supplier-agnostic and Shopify-agnostic.

```
canonical_products
  id             UUID PK
  canonical_sku  TEXT UNIQUE  ← stable slug: brand-name[-size_ml]
  name / brand / size_ml / ean
  pricing_enabled / target_margin_rate / min_margin_abs / shipping_cost_default
  last_price / last_price_at
```

### How supplier_products maps to canonical

`supplier_products` now has `canonical_product_id` (FK → canonical_products).
Each row = one supplier's listing of a canonical product.

```
canonical_products (1) ──── (N) supplier_products
                               supplier, supplier_product_id, supplier_product_url
                               price, stock_status, last_checked_at
```

Constraints:
- `UNIQUE(canonical_product_id, supplier)` – one row per supplier per canonical product
- `UNIQUE(supplier, supplier_product_id)` – supplier-scoped SKU uniqueness

`product_id` is kept for backward compatibility (pre-Sprint-8 rows).

### How shopify_mappings works

```
canonical_products (1) ──── (0..1) shopify_mappings
                                   shopify_product_id
                                   shopify_variant_id  ← used by pricing engine
                                   shopify_inventory_item_id
```

One canonical product → at most one Shopify variant.

### How to run backfill

```bash
# Via Admin API (OPERATOR role required):
POST /admin/canonical/backfill

# Or run migrations against your PostgreSQL DB:
psql $DATABASE_URL < migrations/0008_canonical_layer.sql
psql $DATABASE_URL < migrations/0009_pricing_engine.sql
```

### How pricing is computed

```
cost        = supplier_price + shipping_cost
sell_price  = cost / (1 - target_margin_rate - fee_rate)
```
Then `enforce_min_margin` ensures `sell_price - cost - shipping - fee >= min_margin_abs`.
Finally, `apply_rounding_usd` rounds to `*.99` (e.g. 19.40 → 19.99).

**Defaults per canonical_product:**
| Setting             | Default |
|---------------------|---------|
| target_margin_rate  | 30 %    |
| min_margin_abs      | $3.00   |
| shipping_cost       | $3.00   |
| fee_rate            | 3 % (global) |

### How to run tasks manually

```bash
# Supplier product sync (canonical-based, every 60 min):
celery -A app.workers.celery_app call workers.tasks_supplier_products.sync_supplier_products

# Pricing sync for all products (every 6 h):
celery -A app.workers.celery_app call workers.tasks_pricing.sync_prices

# Pricing sync for one canonical product (on demand):
celery -A app.workers.celery_app call workers.tasks_pricing.sync_price_for_canonical \
  --args '["<canonical-product-uuid>"]'
```

### New Admin Endpoints

| Method | Path | Role | Description |
|--------|------|------|-------------|
| GET | `/admin/canonical/products` | VIEWER | List all canonical products |
| GET | `/admin/canonical/products/{id}` | VIEWER | Get one canonical product |
| GET | `/admin/canonical/products/{id}/suppliers` | VIEWER | Supplier rows for canonical |
| POST | `/admin/canonical/backfill` | OPERATOR | Backfill canonical_product_id |
| GET | `/admin/pricing/quotes` | VIEWER | List recent price quotes |
| POST | `/admin/pricing/sync` | OPERATOR | Trigger full pricing sync |
| POST | `/admin/pricing/canonical/{id}/sync` | OPERATOR | Sync price for one product |

### Testing

```bash
# Fast (mock-only, no network):
make test-fast
# or:
pytest -q -m "not integration and not slow" --maxfail=1

# Full suite (includes integration):
make test
```

CI green is the final gate.

### New files in Sprint 8

```
migrations/
  0008_canonical_layer.sql          – canonical_products, shopify_mappings, backfill
  0009_pricing_engine.sql           – price_quotes table

app/models/
  canonical_product.py              – CanonicalProduct ORM model
  shopify_mapping.py                – ShopifyMapping ORM model
  price_quote.py                    – PriceQuote ORM model

app/services/
  canonical_service.py              – make_canonical_sku, get_or_create, attach_supplier
  pricing_rules.py                  – Pure compute_price, rounding, min_margin
  pricing_service.py                – generate_quote, apply_quote_to_shopify

app/workers/
  tasks_pricing.py                  – sync_prices (6h), sync_price_for_canonical

Modified:
  app/models/supplier_product.py    – added canonical_product_id, supplier_product_url
  app/models/product.py             – added canonical_product_id
  app/services/supplier_router.py   – choose_best_supplier_for_canonical (Sprint 8 primary)
  app/services/shopify_product_service.py – update_variant_price_by_id
  app/workers/tasks_supplier_products.py  – canonical-based sync, legacy fallback
  app/workers/celery_app.py         – added tasks_pricing, sync-prices-every-6h schedule
  app/routers/admin.py              – canonical + pricing endpoints

tests/
  test_sprint8_canonical_mapping.py    – 12 tests: SKU gen, get_or_create, backfill
  test_sprint8_supplier_router_canonical.py – 10 tests: canonical routing, beat schedule
  test_sprint8_pricing_rules.py        – 12 tests: pure rounding + margin
  test_sprint8_pricing_service.py      – 10 tests: quote generation + Shopify apply
  test_sprint8_migrations_or_schema.py – 10 tests: ORM schema inspection
```

---

## Sprint 9: Multi-Channel Commerce Engine

### Overview

Sprint 9 introduces a **Multi-Channel Sales Engine** that extends the canonical product layer (Sprint 8) to support simultaneous selling across multiple platforms:

| Channel | Type | Status |
|---|---|---|
| **Shopify** | Owned store | Active (adapter) |
| **Shopee** | Marketplace | Stub (API wired in future sprint) |
| **TikTok Shop** | Marketplace | Stub (API wired in future sprint) |

All channels share the same `canonical_product` identity – a single product is created once and published everywhere.

---

### Architecture

```
Supplier Products
      ↓
Canonical Product  ←→  Pricing Engine
      ↓
  Channel Router
  ┌────────────────────────────┐
  │  Shopify  Shopee  TikTok  │
  └────────────────────────────┘
      ↓            ↓
channel_products  channel_orders
```

#### Flow: Supplier → Pricing → Publish

1. **Supplier sync** (`tasks_supplier_products`) upserts prices into `supplier_products`
2. **Pricing engine** (`tasks_pricing`) computes `rounded_price` and writes `price_quotes`
3. **Channel publish** (`tasks_channels.publish_new_products`) calls `channel_router.publish_product_to_channels` for each canonical product not yet listed on all channels
4. **Price sync** (`tasks_channels.sync_prices_channels`) pushes the latest price to every channel every 6 h
5. **Inventory sync** (`tasks_channels.sync_inventory_channels`) reads `supplier_products.stock_status` and calls `update_inventory` on each channel every 1 h
6. **Order import** (`tasks_channels.import_channel_orders`) fetches orders from all channels every 15 min and stores them in `channel_orders`

---

### Database Schema (migration `0010_sales_channels.sql`)

#### `sales_channels`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `name` | VARCHAR(32) UNIQUE | slug: shopify / shopee / tiktok_shop |
| `type` | VARCHAR(32) | owned_store \| marketplace |
| `enabled` | BOOLEAN | soft-disable flag |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

Seeded automatically: `shopify`, `shopee`, `tiktok_shop`.

#### `channel_products`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `canonical_product_id` | UUID FK | → canonical_products |
| `channel` | VARCHAR(32) | channel slug |
| `external_product_id` | VARCHAR(128) | platform product ID |
| `external_variant_id` | VARCHAR(128) | platform variant/SKU ID |
| `price` | NUMERIC(12,2) | last-synced sell price |
| `currency` | VARCHAR(8) | default USD |
| `status` | VARCHAR(32) | active \| inactive \| error |

Constraint: `UNIQUE(channel, external_variant_id)`

#### `channel_orders`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `channel` | VARCHAR(32) | |
| `external_order_id` | VARCHAR(128) | platform order ID |
| `canonical_product_id` | UUID FK nullable | |
| `quantity` | INTEGER | |
| `price` | NUMERIC(12,2) | unit price at time of order |
| `status` | VARCHAR(32) | pending \| processing \| completed \| cancelled |

---

### Channel Client Interface (`app/channels/base.py`)

```python
class ChannelClient(ABC):
    async def create_product(canonical_product, *, price) -> dict
    async def update_price(external_variant_id, new_price, currency) -> bool
    async def update_inventory(external_variant_id, quantity) -> bool
    async def fetch_orders(*, limit, status) -> list[dict]
```

All implementations gracefully degrade to **stub mode** when credentials are absent.

---

### Channel Router (`app/services/channel_router.py`)

| Function | Description |
|---|---|
| `get_enabled_channels()` | Returns `['shopify', 'shopee', 'tiktok_shop']` |
| `publish_product_to_channels(cp, *, price, clients)` | Calls `create_product` on all enabled clients |
| `update_price_all_channels(cp, new_price, *, channel_variant_map, clients)` | Pushes new price to mapped channels |
| `update_inventory_all_channels(cp, quantity, *, channel_variant_map, clients)` | Pushes inventory update to mapped channels |

All functions accept an injectable `clients` dict for unit testing without network calls.

---

### Celery Beat Schedule (Sprint 9 additions)

| Task | Schedule | Description |
|---|---|---|
| `publish_new_products` | Every 12 h | Publish unlisted canonical products |
| `sync_prices_channels` | Every 6 h | Push pricing engine prices to channels |
| `sync_inventory_channels` | Every 1 h | Push stock status from supplier_products |
| `import_channel_orders` | Every 15 min | Fetch orders from all channels |

---

### Admin API Endpoints (Sprint 9)

All endpoints require Bearer JWT.

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/admin/channels` | VIEWER | List all sales channels |
| GET | `/admin/channels/products/{canonical_id}` | VIEWER | Channel listings for a canonical product |
| POST | `/admin/channels/publish/{canonical_id}` | OPERATOR | Publish product to all channels |
| POST | `/admin/channels/sync-prices` | OPERATOR | Trigger price sync Celery task |
| POST | `/admin/channels/sync-inventory` | OPERATOR | Trigger inventory sync Celery task |
| GET | `/admin/channels/orders` | VIEWER | List channel orders (filters: channel, status) |

---

### Manual Task Commands

```bash
# Publish all un-listed canonical products
celery -A app.workers.celery_app call workers.tasks_channels.publish_new_products

# Sync prices to all channels
celery -A app.workers.celery_app call workers.tasks_channels.sync_prices_channels

# Sync inventory to all channels
celery -A app.workers.celery_app call workers.tasks_channels.sync_inventory_channels

# Import orders from all channels
celery -A app.workers.celery_app call workers.tasks_channels.import_channel_orders
```

---

### Backfill Procedure

If `canonical_products` already exist but `channel_products` rows are missing:

```bash
# Via Admin API (per product)
curl -X POST /admin/channels/publish/{canonical_id} \
  -H "Authorization: Bearer <token>"

# Via Celery task (all products)
celery -A app.workers.celery_app call workers.tasks_channels.publish_new_products
```

---

### Testing Commands

```bash
# Sprint 9 tests only
pytest tests/test_sprint9_channels_router.py tests/test_sprint9_publish_worker.py \
       tests/test_sprint9_price_sync.py tests/test_sprint9_inventory_sync.py -v

# Full fast suite (229+ tests)
make test-fast
# OR
pytest -q -m "not integration and not slow" --maxfail=1
```

---

### New Files (Sprint 9)

```
migrations/0010_sales_channels.sql          – Idempotent DB migration
app/models/sales_channel.py                 – ORM: SalesChannel, ChannelProduct, ChannelOrder
app/channels/__init__.py                    – Package exports
app/channels/base.py                        – Abstract ChannelClient interface
app/channels/shopify.py                     – Shopify adapter (wraps ShopifyProductService)
app/channels/shopee.py                      – Shopee stub (API placeholders)
app/channels/tiktok_shop.py                 – TikTok Shop stub (API placeholders)
app/services/channel_router.py              – Multi-channel routing functions
app/workers/tasks_channels.py               – Celery tasks (publish, price, inventory, orders)
tests/test_sprint9_channels_router.py       – 21 tests: routing logic
tests/test_sprint9_publish_worker.py        – 13 tests: publish task + beat schedule
tests/test_sprint9_price_sync.py            – 12 tests: price sync task
tests/test_sprint9_inventory_sync.py        – 14 tests: inventory sync + order import
```

### Modified Files (Sprint 9)

```
app/workers/celery_app.py   – Added tasks_channels include + 4 beat schedule entries
app/routers/admin.py        – Added 6 channel management endpoints
```

---

## Production Deployment Guide

### Server Specifications

| Item | Value |
|---|---|
| OS | Ubuntu 24 LTS |
| Public IP | 172.86.127.238 |
| Deploy user | `deploy` |
| Stack | Docker Compose + Nginx |
| External ports | 80 (HTTP), 443 (HTTPS only) |
| Internal ports | 8000 (API), 3001 (Dashboard) – localhost only |

---

### Security Architecture

```
Internet
   │
   ▼  ports 80/443 only
┌─────────────────────────────────┐
│  Nginx (reverse proxy)          │
│  /          → :3001 dashboard   │
│  /admin/    → :8000 FastAPI     │
│  /api/      → :8000 FastAPI     │
│  /health    → :8000 FastAPI     │
└──────────┬──────────────────────┘
           │ 127.0.0.1 only
  ┌────────┴────────────────────┐
  │  Docker Internal Network    │
  │  (kbeauty_internal bridge)  │
  │                             │
  │  :8000 API (FastAPI)        │
  │  :3001 Dashboard (Next.js)  │
  │  :5432 PostgreSQL ← HIDDEN  │
  │  :6379 Redis      ← HIDDEN  │
  └─────────────────────────────┘
```

- PostgreSQL and Redis are **never exposed** to the host or internet
- API and Dashboard are bound to `127.0.0.1` (localhost only)
- Only Nginx receives external traffic on ports 80/443
- All containers use `restart: always` for auto-recovery

---

### Quick Start (Fresh VPS)

```bash
# 1. SSH into server as root
ssh root@172.86.127.238

# 2. Create deploy user
adduser deploy
usermod -aG sudo deploy

# 3. Clone and run deployment script
git clone https://github.com/vinsenzo83/kbeauty-autocommerce.git /opt/apps/kbeauty-autocommerce
cd /opt/apps/kbeauty-autocommerce

# 4. Edit environment variables (REQUIRED before first start)
cp .env.production .env
nano .env   # Fill in all ← REQUIRED values

# 5. Deploy
sudo bash infra/scripts/deploy.sh
```

---

### Step-by-Step Deployment Commands

#### Step 1 – Server preparation
```bash
# Run as root
apt update && apt upgrade -y
apt install -y git nginx ufw ca-certificates curl make

# Install Docker
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
apt install -y docker-compose-plugin

# Firewall
ufw allow OpenSSH
ufw allow 80
ufw allow 443
ufw --force enable
```

#### Step 2 – App directory
```bash
mkdir -p /opt/apps
chown -R deploy:deploy /opt/apps
usermod -aG docker deploy
```

#### Step 3 – Clone repository
```bash
cd /opt/apps
git clone https://github.com/vinsenzo83/kbeauty-autocommerce.git
cd kbeauty-autocommerce
git checkout main
```

#### Step 4 – Environment setup
```bash
cp .env.production .env
chmod 600 .env
nano .env   # Fill in POSTGRES_PASSWORD, JWT_SECRET, SHOPIFY_*, ADMIN_* etc.
```

Key variables to set:
```env
POSTGRES_PASSWORD=<strong-random-32-char>
JWT_SECRET=<strong-random-64-char>
ADMIN_EMAIL=admin@yourdomain.com
ADMIN_PASSWORD=<strong-password>
SHOPIFY_WEBHOOK_SECRET=<from-shopify-partner-dashboard>
SHOPIFY_API_KEY=<shopify-private-app-key>
SHOPIFY_API_SECRET=<shopify-private-app-secret>
SHOPIFY_ACCESS_TOKEN=<shopify-access-token>
SHOPIFY_STORE_DOMAIN=your-store.myshopify.com
STYLEKOREAN_EMAIL=<your-account>
STYLEKOREAN_PASSWORD=<your-password>
```

#### Step 5 – Start application stack
```bash
# Option A: Using Makefile (recommended)
make prod-up

# Option B: Direct Docker Compose
docker compose -f infra/docker-compose.prod.yml --env-file .env up -d --build
```

#### Step 6 – Run database migrations
```bash
# Option A: Using Makefile
make prod-migrate

# Option B: Direct
docker compose -f infra/docker-compose.prod.yml --env-file .env run --rm migrate
```

#### Step 7 – Health verification
```bash
# API health
curl http://127.0.0.1:8000/health

# Dashboard
curl http://127.0.0.1:3001

# Celery worker
docker exec kbeauty-worker celery -A app.workers.celery_app:celery_app inspect ping

# Full health check script
make prod-health
```

#### Step 8 – Nginx reverse proxy
```bash
# Copy config
sudo cp infra/nginx/kbeauty.conf /etc/nginx/sites-available/kbeauty
sudo ln -sf /etc/nginx/sites-available/kbeauty /etc/nginx/sites-enabled/kbeauty
sudo rm -f /etc/nginx/sites-enabled/default

# Test and reload
sudo nginx -t
sudo systemctl reload nginx
```

---

### Expected Docker Containers

```
CONTAINER NAME        IMAGE                    STATUS       PORTS
kbeauty-postgres      postgres:16-alpine       healthy      (internal only)
kbeauty-redis         redis:7-alpine           healthy      (internal only)
kbeauty-api           kbeauty-api:latest       healthy      127.0.0.1:8000→8000
kbeauty-worker        kbeauty-api:latest       running      (no port)
kbeauty-beat          kbeauty-api:latest       running      (no port)
kbeauty-dashboard     kbeauty-dashboard:latest healthy      127.0.0.1:3001→3001
```

---

### Database Backup & Restore

```bash
# Manual backup
make prod-backup
# OR
docker exec -t kbeauty-postgres pg_dump -U kbeauty kbeauty \
  | gzip > /opt/apps/backups/kbeauty_db_$(date +%Y%m%d_%H%M%S).sql.gz

# Restore
gunzip -c /opt/apps/backups/kbeauty_db_YYYYMMDD_HHMMSS.sql.gz \
  | docker exec -i kbeauty-postgres psql -U kbeauty -d kbeauty

# Automated daily backup (add to crontab)
crontab -e
# Add: 0 2 * * * /opt/apps/kbeauty-autocommerce/infra/scripts/backup.sh >> /var/log/kbeauty-backup.log 2>&1
```

---

### SSL Certificate (HTTPS)

```bash
# Install certbot
sudo apt install -y certbot python3-certbot-nginx

# Obtain certificate
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com

# Auto-renewal is configured automatically by certbot
# Test renewal:
sudo certbot renew --dry-run
```

After certbot, edit `/etc/nginx/sites-available/kbeauty`:
- Uncomment the `HTTP → HTTPS redirect` block  
- Uncomment the `HTTPS server` block  
- Comment out the HTTP catch-all block

---

### Logging

```bash
# All containers
make prod-logs

# Individual containers
docker logs -f kbeauty-api
docker logs -f kbeauty-worker
docker logs -f kbeauty-beat
docker logs -f kbeauty-dashboard
docker logs -f kbeauty-postgres

# Nginx logs
sudo tail -f /var/log/nginx/kbeauty_access.log
sudo tail -f /var/log/nginx/kbeauty_error.log
```

---

### Zero-Downtime Updates

```bash
# Pull latest code and redeploy (preserves DB data)
make prod-update

# Manual equivalent
cd /opt/apps/kbeauty-autocommerce
git pull --rebase origin main
make prod-build
make prod-migrate
docker compose -f infra/docker-compose.prod.yml up -d --no-deps api worker beat dashboard
make prod-health
```

---

### Troubleshooting

| Symptom | Command | Solution |
|---|---|---|
| API not responding | `docker logs kbeauty-api` | Check DB connectivity, `.env` vars |
| Celery not running | `docker logs kbeauty-worker` | Check Redis URL, worker concurrency |
| Dashboard blank | `docker logs kbeauty-dashboard` | Check Next.js build, `NEXT_PUBLIC_API_URL` |
| Nginx 502 | `nginx -t && systemctl status nginx` | Confirm API/Dashboard are running |
| DB connection error | `docker exec kbeauty-postgres pg_isready -U kbeauty` | Check `POSTGRES_PASSWORD` |
| Migration failed | `docker logs kbeauty-migrate` | Check SQL syntax, DB connectivity |
| Port not accessible | `ufw status` | Allow port 80/443 in UFW |

```bash
# Check all containers status
make prod-ps

# Run full health check
make prod-health

# Check specific container
docker inspect kbeauty-api | grep -A5 '"Health"'

# Enter running container for debugging
docker exec -it kbeauty-api bash

# Check Celery task queue
docker exec kbeauty-worker \
  celery -A app.workers.celery_app:celery_app inspect active
```

---

### New Files Added for Production (Sprint 9+)

```
infra/docker-compose.prod.yml   – Production Docker Compose (security hardened)
infra/Dockerfile.dashboard      – Multi-stage Next.js production build
infra/migrate.py                – SQL migration runner (used by migrate service)
infra/nginx/kbeauty.conf        – Nginx reverse proxy configuration
infra/scripts/deploy.sh         – Full automated deployment script
infra/scripts/backup.sh         – Database backup + rotation script
infra/scripts/healthcheck.sh    – Service health verification script
.env.production                 – Production .env template (safe to commit)
```

---

## Sprint 11: Webhook Security — Shopify HMAC Verification

### Overview

Sprint 11 adds **production-grade webhook signature verification** for all
incoming Shopify webhook requests.  Shopee and TikTok endpoints are intentionally
left without signature enforcement for now (their signing schemes differ and will
be added in a future sprint).

---

### How Shopify HMAC Verification Works

Shopify signs every webhook with an HMAC-SHA256 digest of the raw request body,
encoded as Base64, and sends it in the `X-Shopify-Hmac-Sha256` HTTP header.

```
expected = base64( hmac_sha256( SHOPIFY_WEBHOOK_SECRET, raw_body ) )
safe_compare( expected, request.headers["X-Shopify-Hmac-Sha256"] )
```

The comparison uses `hmac.compare_digest()` to prevent **timing-oracle attacks**.

---

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `WEBHOOK_VERIFY` | `0` | `0` = dev/test (skip all checks) · `1` = production (enforce HMAC) |
| `SHOPIFY_WEBHOOK_SECRET` | `test-secret` | Shared secret from Shopify Partner Dashboard |

> **Never** set `WEBHOOK_VERIFY=0` in production.

---

### Dev vs Production Mode

#### Development / Test (default)

```dotenv
WEBHOOK_VERIFY=0
SHOPIFY_WEBHOOK_SECRET=test-secret
```

All webhooks are accepted without signature checking.  
Mock fixtures and `scripts/mock_webhooks.sh` work out-of-the-box.

#### Production

```dotenv
WEBHOOK_VERIFY=1
SHOPIFY_WEBHOOK_SECRET=<your-real-shopify-webhook-secret>
```

Every `POST /webhook/shopify` request is verified.  
A failed or missing signature returns **HTTP 401** with:

```json
{
  "status": "unauthorized",
  "reason": "SHOPIFY_WEBHOOK_SIGNATURE_INVALID",
  "detail": "Invalid X-Shopify-Hmac-Sha256 signature"
}
```

---

### Enabling Verification on the VPS

```bash
ssh root@172.86.127.238
cd /opt/apps/kbeauty-autocommerce

# 1. Set production secret from Shopify Partner Dashboard
#    Settings → Notifications → Webhooks → Signing secret
sed -i 's/^SHOPIFY_WEBHOOK_SECRET=.*/SHOPIFY_WEBHOOK_SECRET=<your-real-secret>/' .env
sed -i 's/^WEBHOOK_VERIFY=.*/WEBHOOK_VERIFY=1/'                                  .env

# 2. Restart API to pick up new env vars
docker compose -f infra/docker-compose.prod.yml --env-file .env \
  up -d --no-deps --force-recreate api

# 3. Verify — valid HMAC should return 200, invalid should return 401
SECRET="<your-real-secret>"
BODY='{"id":1,"test":true}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -binary | base64)

curl -s -X POST http://172.86.127.238/webhook/shopify \
  -H "Content-Type: application/json" \
  -H "X-Shopify-Topic: orders/create" \
  -H "X-Shopify-Hmac-Sha256: $SIG" \
  -d "$BODY" | python3 -m json.tool

# Tampered signature → must return 401
curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://172.86.127.238/webhook/shopify \
  -H "Content-Type: application/json" \
  -H "X-Shopify-Hmac-Sha256: invalidsignature==" \
  -d "$BODY"
# Expected output: 401
```

---

### New Files (Sprint 11)

```
app/webhooks/verify.py                  – verify_shopify_webhook() pure function
tests/test_sprint11_webhook_verify.py   – 13 tests (unit + integration)
```

### Modified Files (Sprint 11)

```
app/config.py           – Added WEBHOOK_VERIFY (bool, default False)
app/webhooks/ingress.py – Integrated signature gate; Settings injected via DI
```

### Test Coverage (Sprint 11)

| Test | Description |
|---|---|
| `test_verify_shopify_valid_signature` | Correct HMAC → True |
| `test_verify_shopify_wrong_secret` | Wrong secret → False |
| `test_verify_shopify_tampered_body` | Modified body → False |
| `test_verify_shopify_empty_header` | Empty header → False |
| `test_verify_shopify_none_inputs` | None/empty values → False |
| `test_ingress_valid_sig_verify_on` | Valid sig + `WEBHOOK_VERIFY=1` → 200 ok |
| `test_ingress_invalid_sig_verify_on` | Invalid sig + `WEBHOOK_VERIFY=1` → 401 |
| `test_ingress_missing_header_verify_on` | Missing header + `WEBHOOK_VERIFY=1` → 401 |
| `test_ingress_verify_disabled` | Bad sig + `WEBHOOK_VERIFY=0` → 200 ok |
| `test_ingress_reason_in_401_body` | 401 body contains `SHOPIFY_WEBHOOK_SIGNATURE_INVALID` |
| `test_ingress_shopee_no_verify` | Shopee bypasses Shopify HMAC check |
| `test_ingress_tiktok_no_verify` | TikTok bypasses Shopify HMAC check |
| `test_existing_sprint10_tests_unaffected` | Sprint 10 flow still works (regression) |

---

### Definition of Done — Sprint 11 ✅

- [x] `POST /webhook/shopify` with invalid signature returns **HTTP 401**
- [x] `POST /webhook/shopify` with valid signature returns **200 ok**
- [x] Verification disabled by default (`WEBHOOK_VERIFY=0`) — no breaking change
- [x] Shopee / TikTok endpoints unaffected
- [x] All 320 tests pass — CI green
- [x] README updated

