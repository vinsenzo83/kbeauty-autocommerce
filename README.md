# kbeauty-autocommerce

K-Beauty 주문 자동화 백엔드 MVP — FastAPI · PostgreSQL · Redis · Celery

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
