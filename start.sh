#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/com.whypuss.agy-telegram-bot.plist"
if [ -f "$PLIST" ]; then
    echo "Loading launchd service..."
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "Service started via launchd. Check status with ./status.sh"
else
    echo "Starting via nohup in background..."
    nohup ./venv/bin/python3 bot.py > bot.log 2>&1 &
    echo "$!" > .bot.pid
    echo "Started PID: $! (saved to .bot.pid)"
fi
