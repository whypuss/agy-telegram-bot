"""
Configuration module for Antigravity Telegram Bot.
Loads settings from environment variables and .env file.
"""

import os
import shutil
from pathlib import Path
from dotenv import load_dotenv

# Load .env from current directory or parent
load_dotenv()

# ---------------------------------------------------------------------------
# Telegram Settings
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Allowed Telegram User IDs (comma-separated). If empty, warning will be logged.
ALLOWED_USER_IDS: set[int] = set()
_raw_user_ids = os.getenv("ALLOWED_USER_IDS", "").strip()
if _raw_user_ids:
    for uid_str in _raw_user_ids.split(","):
        uid_str = uid_str.strip()
        if uid_str and uid_str.isdigit():
            ALLOWED_USER_IDS.add(int(uid_str))

# Network Proxy (HTTP, HTTPS, SOCKS5)
# e.g., http://127.0.0.1:7890 or socks5://127.0.0.1:1080
PROXY_URL: str = os.getenv("PROXY_URL", "").strip()

# Group Mention Policy
# When True, the bot in group chats will only respond if mentioned (@botname) or replied to.
REQUIRE_MENTION_IN_GROUPS: bool = os.getenv("REQUIRE_MENTION_IN_GROUPS", "true").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# Antigravity CLI (agy) Settings
# ---------------------------------------------------------------------------
# Locate agy binary
_agy_env_path = os.getenv("AGY_PATH", "").strip()
if _agy_env_path:
    AGY_PATH = _agy_env_path
else:
    # Try finding in PATH or common standard locations
    AGY_PATH = shutil.which("agy") or shutil.which("gemini") or "agy"

# Default Model
DEFAULT_MODEL: str = os.getenv("AGY_MODEL", "Gemini 3.7 Flash").strip()

# Timeout per agent turn (in seconds)
AGY_TIMEOUT: int = int(os.getenv("AGY_TIMEOUT", "300"))

# Default working directory for the agent
WORKSPACE_DIR: str = os.getenv("WORKSPACE_DIR", str(Path.home())).strip()

# System prompt override (optional)
AGENT_SYSTEM_PROMPT: str = os.getenv("AGENT_SYSTEM_PROMPT", "").strip()

# ---------------------------------------------------------------------------
# Message & Media Batching Settings
# ---------------------------------------------------------------------------
# Telegram limit is 4096 UTF-16 code units
TG_MAX_MESSAGE_LENGTH: int = 4096

# Time to buffer rapid text messages (handles client-side split pasting)
TEXT_BATCH_DELAY_SECONDS: float = float(os.getenv("TEXT_BATCH_DELAY_SECONDS", "0.6"))

# Time to buffer rapid photo bursts or albums
MEDIA_BATCH_DELAY_SECONDS: float = float(os.getenv("MEDIA_BATCH_DELAY_SECONDS", "0.8"))

# Cache directory for downloaded user media (images, voice, files)
CACHE_DIR: Path = Path(os.getenv("CACHE_DIR", str(Path.home() / ".gemini" / "tg_media_cache")))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Auth Helper
# ---------------------------------------------------------------------------
def is_authorized(user_id: int) -> bool:
    """Check if the user is in the allowed user set. If set is empty, allow all."""
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS
