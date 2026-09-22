#!/usr/bin/env bash
# start_ide_cdp.sh - Launch Antigravity IDE with CDP debugging port enabled

PORT=${1:-9334}
APP_NAME="Antigravity IDE"

echo "========================================================"
echo "  ⚡ Launching Antigravity IDE with CDP Port: $PORT"
echo "========================================================"

# Check if port is already listening
if lsof -i :"$PORT" >/dev/null 2>&1; then
    echo "✅ Antigravity IDE is already listening on CDP port $PORT!"
    exit 0
fi

# Check if an instance is already running without the port
PID=$(ps -ef | grep "[A]ntigravity IDE.app/Contents/MacOS/Electron" | awk '{print $2}' || true)
if [ -n "$PID" ]; then
    echo "⚠️  Antigravity IDE is already running (PID: $PID) WITHOUT the debugging port."
    echo "ℹ️  macOS requires closing the existing instance first to apply --remote-debugging-port."
    echo "Closing running instance..."
    osascript -e 'tell application "Antigravity IDE" to quit' 2>/dev/null || kill "$PID" 2>/dev/null || true
    for _ in {1..10}; do
        if ! kill -0 "$PID" 2>/dev/null; then
            break
        fi
        sleep 1
    done
    if kill -0 "$PID" 2>/dev/null; then
        echo "Force terminating PID $PID..."
        kill -9 "$PID" 2>/dev/null || true
        sleep 1
    fi
fi

echo "🚀 Starting Antigravity IDE with --remote-debugging-port=$PORT..."
open -a "$APP_NAME" --args --remote-debugging-port="$PORT"

# Wait up to 10 seconds for CDP port to open
echo "Waiting for CDP port $PORT to become ready..."
for i in {1..10}; do
    if lsof -i :"$PORT" >/dev/null 2>&1; then
        echo "✅ Antigravity IDE is now ONLINE on port $PORT!"
        echo "You can now use /ide and /screenshot on Telegram!"
        exit 0
    fi
    sleep 1
done

echo "⚠️ Timed out waiting for port $PORT. Please check if Antigravity IDE opened."
