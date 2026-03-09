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
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir yt-dlp \
    && curl -sSL https://bin.ngrok.com/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz -o /tmp/ngrok.tgz \
    && tar -xzf /tmp/ngrok.tgz -C /usr/local/bin \
    && rm /tmp/ngrok.tgz

WORKDIR /app

COPY --from=builder /install /usr/local

COPY backend/requirements.txt ./
COPY backend/api.py ./
COPY backend/main.py ./
COPY backend/start.py ./
COPY backend/reset.py ./
COPY backend/core/ ./core/
COPY backend/analyzers/ ./analyzers/
COPY backend/instagram/ ./instagram/
COPY frontend/ ./frontend/
COPY backend/static/ ./static/
COPY backend/config/ ./config/
COPY docker-entrypoint.sh ./

RUN chmod +x docker-entrypoint.sh && \
    mkdir -p temp static && \
    mkdir -p /app/data && \
    useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 5000

ENTRYPOINT ["./docker-entrypoint.sh"]
