#!/bin/bash
set -e

# Auto-generate SECRET_KEY if not provided so the app boots out-of-the-box
if [ -z "$SECRET_KEY" ]; then
    SECRET_FILE="${DATA_DIR:-/app/data}/.secret_key"
    mkdir -p "$(dirname "$SECRET_FILE")"
    if [ ! -f "$SECRET_FILE" ]; then
        echo "🔑 No SECRET_KEY provided — generating one (saved to $SECRET_FILE)"
        head -c 48 /dev/urandom | base64 | tr -d '\n' > "$SECRET_FILE"
    fi
    export SECRET_KEY="$(cat "$SECRET_FILE")"
fi

# 【已彻底移除 Arduino 编译器的初始化和下载逻辑】
# 你的自定义芯片将通过 WASI-SDK 编译，不受影响。

# Start FastAPI backend in the background on port 8001
echo "🚀 Starting Velxio Backend..."
uvicorn app.main:app --host 127.0.0.1 --port 8001 &
UVICORN_PID=$!

# Wait for backend to be healthy before starting nginx
sleep 2

# Start Nginx in the background (not exec — we need to monitor both)
echo "🌐 Starting Nginx Web Server on port 80..."
nginx -g "daemon off;" &
NGINX_PID=$!

# Exit as soon as either process dies so Docker can restart the container.
wait -n $UVICORN_PID $NGINX_PID
EXIT_CODE=$?

echo "⚠️  A process exited (code $EXIT_CODE) — shutting down container"
kill $UVICORN_PID $NGINX_PID 2>/dev/null || true
wait $UVICORN_PID $NGINX_PID 2>/dev/null || true
exit $EXIT_CODE
