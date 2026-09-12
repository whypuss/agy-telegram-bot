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
    import ssl
    ctx = ssl._create_unverified_context()
    last_err = None
    # Measured ~1.5% transient failures (SSL EOF, connection reset) against this
    # host's transparent proxy. One blip is not an outage, so retry before
    # reporting unreachable.
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Watchdog/1.0)"})
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(2)
    log(f"[Watchdog] Telegram API probe error after 3 attempts: {last_err}")
    return False


# ---------------------------------------------------------------------------
# Alive-but-broken detection
#
# A live process passing an API probe is not proof the bot works. On 2026-09-12
# it polled getUpdates successfully for four hours while every outbound reply
# failed: a file descriptor leak had exhausted launchd's 256 soft limit, so new
# sockets could not be created while the existing keep-alive connection kept
# working. Liveness alone would never have caught it. These two checks would.
# ---------------------------------------------------------------------------

ERROR_LOG = BASE_DIR / "bot.err.log"
ERROR_WINDOW_SECONDS = 600      # look back 10 minutes
ERROR_STORM_THRESHOLD = 50      # sustained errors, not the odd blip
FD_USAGE_RATIO = 0.8            # fraction of the soft limit that counts as a leak
RESTART_COOLDOWN_SECONDS = 900  # never restart more than once per 15 minutes
LAST_RESTART_FILE = BASE_DIR / ".watchdog_last_restart"


def _recent_error_count() -> int:
    """Count ERROR lines logged within the lookback window."""
    if not ERROR_LOG.exists():
        return 0
    cutoff = time.time() - ERROR_WINDOW_SECONDS
    count = 0
    try:
        with open(ERROR_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            # 2MB of tail is far more than a 10-minute window ever produces.
            f.seek(max(0, f.tell() - 2_000_000))
            for raw in f.read().decode("utf-8", "replace").splitlines():
                if "[ERROR]" not in raw:
                    continue
                try:
                    stamp = time.mktime(time.strptime(raw[:19], "%Y-%m-%d %H:%M:%S"))
                except Exception:
                    continue
                if stamp >= cutoff:
                    count += 1
    except Exception as e:
        log(f"[Watchdog] error-log scan failed: {e}")
    return count


def _fd_pressure() -> tuple:
    """Return (open_fds, soft_limit) for the bot process, or (0, 0) if unknown."""
    try:
        res = subprocess.run(["pgrep", "-f", "projects/agy-telegram-bot/bot.py"],
                             capture_output=True, text=True)
        pid = res.stdout.strip().splitlines()[0] if res.stdout.strip() else None
        if not pid:
            return (0, 0)
        res = subprocess.run(["lsof", "-p", pid], capture_output=True, text=True)
        open_fds = max(0, len(res.stdout.strip().splitlines()) - 1)

        limit = 0
        res = subprocess.run(["launchctl", "limit", "maxfiles"], capture_output=True, text=True)
        parts = res.stdout.split()
        if len(parts) >= 2 and parts[1].isdigit():
            limit = int(parts[1])
        return (open_fds, limit)
    except Exception as e:
        log(f"[Watchdog] fd probe failed: {e}")
        return (0, 0)


def _restart_allowed() -> bool:
    """Rate-limit restarts so a persistent fault cannot become a restart loop."""
    try:
        if LAST_RESTART_FILE.exists():
            last = float(LAST_RESTART_FILE.read_text().strip())
            if time.time() - last < RESTART_COOLDOWN_SECONDS:
                return False
    except Exception:
        pass
    return True


def _mark_restart() -> None:
    try:
        LAST_RESTART_FILE.write_text(str(time.time()))
    except Exception:
        pass

def is_process_running() -> bool:
    # 1. Check pgrep
    try:
        res = subprocess.run(["pgrep", "-f", "projects/agy-telegram-bot/bot.py"], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return True
    except Exception:
        pass

    # Fallback: match any python bot.py, but only count it if its cwd is this
    # directory. A bare `python.*bot\.py` also matches drawbot, which would make
    # the watchdog believe a dead bot is alive and never restart it.
    try:
        res = subprocess.run(["pgrep", "-f", "[Pp]ython.*bot\\.py"], capture_output=True, text=True)
        for pid in res.stdout.split():
            cwd = subprocess.run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                                 capture_output=True, text=True).stdout
            for line in cwd.splitlines():
                if line.startswith("n") and line[1:] == str(BASE_DIR):
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

def check_alive_but_broken() -> bool:
    """Detect a running process that is no longer doing its job."""
    open_fds, limit = _fd_pressure()
    if limit and open_fds >= limit * FD_USAGE_RATIO:
        log(f"[Watchdog] fd pressure: {open_fds}/{limit} open — descriptor leak suspected.")
        return True

    errors = _recent_error_count()
    if errors >= ERROR_STORM_THRESHOLD:
        log(f"[Watchdog] error storm: {errors} ERROR lines in the last "
            f"{ERROR_WINDOW_SECONDS // 60} min (threshold {ERROR_STORM_THRESHOLD}).")
        return True

    return False


def check():
    if not is_process_running():
        log("[Watchdog] Process not running. Initiating restart.")
        restart_service()
        _mark_restart()
        return

    # Check network reachability
    if not is_telegram_api_reachable():
        log("[Watchdog] Warning: Telegram API unreachable from this host.")
        return

    # Process is up and the network is fine — but is the bot actually working?
    if check_alive_but_broken():
        if _restart_allowed():
            log("[Watchdog] Alive but broken. Restarting.")
            restart_service()
            _mark_restart()
        else:
            log(f"[Watchdog] Alive but broken — restart suppressed, "
                f"last restart was under {RESTART_COOLDOWN_SECONDS // 60} min ago.")

if __name__ == "__main__":
    check()
