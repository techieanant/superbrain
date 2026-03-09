#!/bin/bash
set -e

echo "Starting SuperBrain Backend..."

cd /app/backend

# Function to get ngrok URL
get_ngrok_url() {
    curl -s localhost:4040/api/tunnels 2>/dev/null | grep -o '"public_url":"https://[^"]*"' | cut -d'"' -f4
}

# Get ngrok token from env or file
NGROK_TOKEN_VALUE=""
if [ -n "$NGROK_TOKEN" ]; then
    NGROK_TOKEN_VALUE="$NGROK_TOKEN"
elif [ -f /app/backend/config/ngrok_token.txt ]; then
    NGROK_TOKEN_VALUE=$(cat /app/backend/config/ngrok_token.txt)
fi

# Start ngrok if token is configured
if [ -n "$NGROK_TOKEN_VALUE" ]; then
    echo "Configuring ngrok with authtoken..."
    ngrok authtoken "$NGROK_TOKEN_VALUE"
    
    echo "Starting ngrok tunnel..."
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
    
    # Print ngrok URL if available
    if [ -f /tmp/ngrok_url.txt ]; then
        echo "=========================================="
        echo "🔗 ngrok Public URL: $(cat /tmp/ngrok_url.txt)"
        echo "=========================================="
    else
        echo "Warning: ngrok started but URL not available yet"
    fi
fi

exec uvicorn api:app --host 0.0.0.0 --port 5000
