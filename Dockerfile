# Multi-stage: full toolchain to build wheels, slim runtime to serve them.
# Release runs `alembic upgrade head`; the web process runs Uvicorn.
# The image never ships a .env -- secrets arrive as environment variables.

FROM python:3.11-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY app/ ./app/
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./
RUN pip install --upgrade pip \
    && pip wheel --no-deps --wheel-dir /wheels .

FROM python:3.11-slim AS runtime
WORKDIR /srv/app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 recoup
COPY --from=builder /wheels /wheels
COPY pyproject.toml alembic.ini ./
COPY app/ ./app/
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY scripts/ ./scripts/
RUN pip install --upgrade pip \
    && pip install --no-cache-dir /wheels/*.whl \
    && pip install --no-cache-dir "uvicorn[standard]>=0.32" alembic "psycopg[binary]>=3.2" aiosqlite \
    && rm -rf /wheels /root/.cache
USER recoup
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)" || exit 1
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
