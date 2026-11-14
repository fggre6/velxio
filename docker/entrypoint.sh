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

# Ensure arduino-cli config and board manager URLs are set up
# 裁剪说明：移除了 ESP32 和 RP2040 的板卡源，只保留基础 AVR 系列
if [ ! -f /root/.arduino15/arduino-cli.yaml ]; then
    echo "📦 Initializing arduino-cli config..."
    arduino-cli config init 2>/dev/null || true
    # ATTinyCore (Spence Konde) — needed for ATtiny85 FQBNs.
    arduino-cli config add board_manager.additional_urls \
        http://drazzy.com/package_drazzy.com_index.json 2>/dev/null || true
fi

# Seed board-manager indexes vendored into the image
if [ -d /opt/arduino15-seed ]; then
    for seed in /opt/arduino15-seed/package_*.json; do
        [ -f "$seed" ] || continue
        dest="/root/.arduino15/$(basename "$seed")"
        if [ ! -f "$dest" ]; then
            echo "Seeding board index $(basename "$seed") (missing from volume)"
            cp "$seed" "$dest"
        fi
    done
fi

# Install missing cores.
# 裁剪说明：只安装最基础的 AVR（如 Arduino Uno/Nano）和 ATTiny，彻底移除 ESP32、RP2040 和 STM32。
echo "📦 Installing basic Arduino cores..."
arduino-cli core update-index 2>/dev/null || true
arduino-cli core install arduino:avr 2>/dev/null || true
arduino-cli core install ATTinyCore:avr@1.4.1 2>/dev/null || true

# 裁剪说明：完全移除了 ESP-IDF 加载逻辑和 ESP32 core 的回退安装逻辑。
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
