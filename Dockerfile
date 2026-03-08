# Stage 1: Build dependencies
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir yt-dlp

WORKDIR /app

COPY --from=builder /install /usr/local

COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY backend/static/ ./static/
COPY backend/config/ ./config/
COPY docker-entrypoint.sh ./

RUN chmod +x docker-entrypoint.sh && \
    mkdir -p temp static && \
    useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 5000

ENTRYPOINT ["./docker-entrypoint.sh"]
