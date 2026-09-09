#!/bin/bash
echo "=== launchctl status ==="
launchctl list | grep agy-telegram-bot || echo "Not loaded in launchctl"
echo ""
echo "=== Running process ==="
ps aux | grep "[b]ot.py" || echo "Process not running"
echo ""
echo "=== Recent logs (bot.log / bot.err.log) ==="
if [ -f bot.err.log ] && [ -s bot.err.log ]; then
    echo "[bot.err.log]"
    tail -n 10 bot.err.log
fi
if [ -f bot.log ]; then
    echo "[bot.log]"
    tail -n 15 bot.log
fi
