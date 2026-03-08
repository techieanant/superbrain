#!/bin/bash
set -e

echo "Starting SuperBrain Backend..."

cd /app/backend

exec uvicorn api:app --host 0.0.0.0 --port 5000
