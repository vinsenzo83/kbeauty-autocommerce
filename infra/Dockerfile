FROM python:3.11-slim

# ── system deps ───────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── workdir ───────────────────────────────────────────────────────────────────
WORKDIR /app

# ── python deps ───────────────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── source ────────────────────────────────────────────────────────────────────
COPY . .

# ── default command (overridden in docker-compose for worker) ─────────────────
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
