# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install system deps (for building wheels & TLS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc curl tini ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -ms /bin/bash appuser
WORKDIR /app

# Copy requirements first (better layer cache)
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt gunicorn==22.0.0

# Copy app
COPY . /app

# Switch to non-root
USER appuser

# Environment defaults (override via .env)
ENV APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    GUNICORN_WORKERS=2 \
    GUNICORN_THREADS=4 \
    GUNICORN_TIMEOUT=30

EXPOSE 8000

# HEALTHCHECK (simple TCP)
HEALTHCHECK --interval=30s --timeout=3s --retries=5 \
  CMD curl -fsS http://127.0.0.1:${APP_PORT}/health || exit 1

ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["gunicorn","-w","2","--threads","4","--timeout","30","-b","0.0.0.0:8000","app:app"]
