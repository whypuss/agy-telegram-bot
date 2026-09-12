#!/usr/bin/env python3
"""
1-Minute Robust Watchdog & Auto-Recovery Monitor for Antigravity Telegram Bot.
Checks process liveness and Telegram API connectivity.
Restarts the bot service automatically if disconnected, crashed, or unloaded.
"""

import os
import sys
import time
import urllib.request
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
LOG_FILE = BASE_DIR / "watchdog.log"

# Load environment
env_file = BASE_DIR / ".env"
token = ""
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            token = line.split("=", 1)[1].strip().strip('"').strip("'")
            break

if not token:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

def log(msg: str):
    ts = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{ts} {msg}\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="")

def is_telegram_api_reachable() -> bool:
    if not token:
        return True
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Watchdog/1.0)"})
        import ssl
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        log(f"[Watchdog] Telegram API probe error: {e}")
        return False

def is_process_running() -> bool:
    # 1. Check pgrep
    try:
        res = subprocess.run(["pgrep", "-f", "projects/agy-telegram-bot/bot.py"], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return True
    except Exception:
        pass

    try:
        res = subprocess.run(["pgrep", "-f", "python.*bot\\.py"], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return True
    except Exception:
        pass

    # 2. Check launchctl list table
    try:
        res = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if "com.whypuss.agy-telegram-bot" in line:
                    parts = line.split()
                    if len(parts) >= 3 and parts[0] != "-":
                        return True
    except Exception:
        pass

    return False

def restart_service():
    log("[Watchdog] Process not running or dead. Initiating restart...")
    uid = os.getuid()
    label = "com.whypuss.agy-telegram-bot"
    plist_path = Path.home() / f"Library/LaunchAgents/{label}.plist"

    # Kickstart or load
    try:
        res = subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"], capture_output=True, text=True)
        if res.returncode == 0:
            log(f"[Watchdog] Successfully kickstarted {label}")
            return
    except Exception:
        pass

    if plist_path.exists():
        subprocess.run(["launchctl", "load", "-w", str(plist_path)], capture_output=True)
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"], capture_output=True)
        log("[Watchdog] Loaded & kickstarted via plist.")
        return

    # Fallback to restart.sh
    restart_script = BASE_DIR / "restart.sh"
    if restart_script.exists():
        subprocess.run(["bash", str(restart_script)], capture_output=True)
        log("[Watchdog] Triggered restart.sh")

def check():
    if not is_process_running():
        log("[Watchdog] Process not running. Initiating restart.")
        restart_service()
        return

    # Check network reachability
    if not is_telegram_api_reachable():
        log("[Watchdog] Warning: Telegram API unreachable from this host.")
        return

if __name__ == "__main__":
    check()
