#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/com.whypuss.agy-telegram-bot.plist"
if [ -f "$PLIST" ]; then
    echo "Unloading launchd service..."
    launchctl unload "$PLIST" 2>/dev/null || true
fi
pkill -f "python3 bot.py" 2>/dev/null || true
echo "Bot stopped."
