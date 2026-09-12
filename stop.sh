#!/bin/bash
LABEL="com.whypuss.agy-telegram-bot"
UID_NUM="$(id -u)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ -f "$PLIST" ]; then
    echo "Stopping launchd service..."
    launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
fi
pkill -f "python.*bot\.py" 2>/dev/null || true
echo "Bot stopped."
