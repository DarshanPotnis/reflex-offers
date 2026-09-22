# syntax=docker/dockerfile:1

# ---------------------------------------------------------------- frontend
# Built first so the Python image never needs node at runtime.
FROM node:24.11.1-bookworm-slim AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# vite.config.ts writes to ../backend/static, so give it that shape.
RUN mkdir -p /backend && npm run build && ls -la /backend/static

# ------------------------------------------------------------------ server
FROM python:3.14.7-slim-bookworm AS server

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first: this layer is cached unless requirements.txt changes.
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/pytest.ini ./
COPY --from=frontend /backend/static ./static

# Render sets $PORT; 8000 is the local default.
ENV PORT=8000
EXPOSE 8000

# Not root. Nothing here writes to disk except a local SQLite file, and in
# production the database is Postgres.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/api/health', timeout=4).status == 200 else 1)"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
