#!/bin/bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.whypuss.agy-telegram-bot"
UID_NUM="$(id -u)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ -f "$PLIST" ]; then
    launchctl load -w "$PLIST" 2>/dev/null || true
    launchctl kickstart -k "gui/$UID_NUM/$LABEL" 2>/dev/null || true
    echo "Service started via launchd. Check status with ./status.sh"
else
    nohup "$DIR/venv/bin/python3" "$DIR/bot.py" > "$DIR/bot.log" 2>&1 &
    echo "Started PID: $!"
fi
