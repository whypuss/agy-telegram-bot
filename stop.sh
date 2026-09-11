#!/bin/bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.whypuss.agy-telegram-bot.plist"
if [ -f "$PLIST" ]; then
    echo "Unloading launchd service..."
    launchctl unload "$PLIST" 2>/dev/null || true
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
#    (Old pattern `python3 bot.py` missed the real `.../Python bot.py`,
#     and a bare `pkill -f bot.py` would also kill drawbot.)
for PID in $(pgrep -f "[Pp]ython.*bot\.py" 2>/dev/null); do
    CWD="$(lsof -a -p "$PID" -d cwd -Fn 2>/dev/null | grep '^n' | cut -c2-)"
    if [ "$CWD" = "$DIR" ]; then
        echo "Stopping stale process PID $PID (cwd=$CWD)..."
        kill "$PID" 2>/dev/null || true
    fi
done
sleep 1

# Report, but never touch bot.py processes outside this DIR (e.g. drawbot)
STILL=""
for PID in $(pgrep -f "[Pp]ython.*bot\.py" 2>/dev/null); do
    CWD="$(lsof -a -p "$PID" -d cwd -Fn 2>/dev/null | grep '^n' | cut -c2-)"
    if [ "$CWD" = "$DIR" ]; then
        STILL="$STILL $PID"
    fi
done
if [ -z "$STILL" ]; then
    echo "Bot stopped. (other bot.py processes outside $DIR left untouched)"
else
    echo "WARNING: still running in $DIR:$STILL"
fi
