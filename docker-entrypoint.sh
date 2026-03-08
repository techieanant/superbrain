#!/bin/bash
set -e

echo "Starting SuperBrain Backend..."

cd /app/backend

# Function to get ngrok URL
get_ngrok_url() {
    curl -s localhost:4040/api/tunnels 2>/dev/null | grep -o '"public_url":"https://[^"]*"' | cut -d'"' -f4
}

# Start ngrok in background if token is configured
if [ -n "$NGROK_TOKEN" ]; then
    echo "Starting ngrok with token..."
    ngrok http 5000 --log=stdout > /tmp/ngrok.log 2>&1 &
    
    # Wait for ngrok to be ready and get URL
    for i in {1..30}; do
        NGROK_URL=$(get_ngrok_url)
        if [ -n "$NGROK_URL" ]; then
            echo "ngrok URL: $NGROK_URL"
            echo "$NGROK_URL" > /tmp/ngrok_url.txt
            break
        fi
        sleep 1
    done
elif [ -f /app/backend/config/ngrok_token.txt ]; then
    echo "Starting ngrok with stored token..."
    ngrok http 5000 --log=stdout > /tmp/ngrok.log 2>&1 &
    
    for i in {1..30}; do
        NGROK_URL=$(get_ngrok_url)
        if [ -n "$NGROK_URL" ]; then
            echo "ngrok URL: $NGROK_URL"
            echo "$NGROK_URL" > /tmp/ngrok_url.txt
            break
        fi
        sleep 1
    done
fi

# Print ngrok URL if available
if [ -f /tmp/ngrok_url.txt ]; then
    echo "=========================================="
    echo "🔗 ngrok Public URL: $(cat /tmp/ngrok_url.txt)"
    echo "=========================================="
fi

exec uvicorn api:app --host 0.0.0.0 --port 5000
