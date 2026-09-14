#!/bin/bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.whypuss.agy-telegram-bot"
UID_NUM="$(id -u)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ -f "$PLIST" ]; then
    echo "Stopping launchd service..."
    launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
fi

# 1) Precise kill via PID file (written by start.sh, avoids touching drawbot etc.)
if [ -f "$DIR/.bot.pid" ]; then
    PID="$(tr -d '[:space:]' < "$DIR/.bot.pid")"
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "Stopping PID $PID..."
        kill "$PID" 2>/dev/null || true
        sleep 2
        if kill -0 "$PID" 2>/dev/null; then
            echo "Force killing PID $PID..."
            kill -9 "$PID" 2>/dev/null || true
        fi
    fi
    rm -f "$DIR/.bot.pid"
fi

# 2) Fallback: kill python bot.py processes whose cwd is this DIR.
#    (Avoids touching drawbot or other bot.py processes)
for PID in $(pgrep -f "[Pp]ython.*bot\.py" 2>/dev/null); do
    CWD="$(lsof -a -p "$PID" -d cwd -Fn 2>/dev/null | grep '^n' | cut -c2-)"
    if [ "$CWD" = "$DIR" ]; then
        echo "Stopping stale process PID $PID (cwd=$CWD)..."
        kill "$PID" 2>/dev/null || true
    fi
done
sleep 1
