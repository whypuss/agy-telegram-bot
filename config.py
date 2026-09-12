"""
Configuration module for Antigravity Telegram Bot.
Loads settings from environment variables and .env file.
"""

import os
import shutil
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env from current directory or parent
load_dotenv()

# ---------------------------------------------------------------------------
# Telegram Settings
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Allowed Telegram User IDs (comma-separated). Strict check enabled.
ALLOWED_USER_IDS: set[int] = set()
_raw_user_ids = os.getenv("ALLOWED_USER_IDS", "").strip() or os.getenv("TELEGRAM_ALLOWED_USERS", "").strip()
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
DEFAULT_MODEL: str = os.getenv("AGY_MODEL", "Gemini 3.8 Flash (Medium)").strip()

# Timeout per agent turn (in seconds, default: 3600 = 1 hour)
AGY_TIMEOUT: int = int(os.getenv("AGY_TIMEOUT", "3600"))

# Default working directory for the agent
WORKSPACE_DIR: str = os.getenv("WORKSPACE_DIR", str(Path.home())).strip()

# Passing --add-dir makes agy preload every SKILL.md description under the
# target's .agents/skills/ into the system prompt — measured at +4,808 tokens,
# re-sent on every tool round. The agent already runs with cwd=WORKSPACE_DIR,
# so it can read/write there without the flag. Set to "1" to restore it.
AGY_ADD_WORKSPACE_DIR: bool = os.getenv("AGY_ADD_WORKSPACE_DIR", "0").strip() == "1"

# System prompt override (optional)
AGENT_SYSTEM_PROMPT: str = os.getenv("AGENT_SYSTEM_PROMPT", "").strip()

# A multi-section operating protocol does not fit on one .env line, and agy only
# reads AGENTS.md when --add-dir is passed (which also drags in every skill
# description). AGENT_PROTOCOL_FILE points at a markdown file that is appended
# to AGENT_SYSTEM_PROMPT instead. A missing file is loud, not silent — a
# protocol that quietly fails to load is worse than no protocol.
_protocol_file = os.getenv("AGENT_PROTOCOL_FILE", "").strip()
if _protocol_file:
    try:
        _protocol = Path(_protocol_file).expanduser().read_text(encoding="utf-8").strip()
        AGENT_SYSTEM_PROMPT = "\n\n".join(p for p in (AGENT_SYSTEM_PROMPT, _protocol) if p)
    except OSError as _e:
        print(f"[config] AGENT_PROTOCOL_FILE unreadable ({_e}) — protocol NOT loaded", file=sys.stderr)

# ---------------------------------------------------------------------------
# Local OpenCode Fallback Settings
# ---------------------------------------------------------------------------
def _resolve_opencode_path() -> str:
    """Locate a working opencode binary (verifies --version, skips broken wrappers)."""
    import glob as _glob
    import subprocess as _sp

    candidates: list[str] = []
    env_path = os.getenv("OPENCODE_PATH", "").strip()
    if env_path:
        candidates.append(env_path)
    try:
        candidates.extend(reversed(sorted(_glob.glob("/opt/homebrew/Cellar/opencode/*/bin/opencode"))))
    except Exception:
        pass
    which_hit = shutil.which("opencode")
    if which_hit:
        candidates.append(which_hit)
    candidates.extend([
        str(Path.home() / ".local" / "bin" / "opencode"),
        "/usr/local/bin/opencode",
    ])

    seen: set[str] = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        try:
            p = Path(cand)
            if p.is_symlink() and not p.exists():
                continue
            if not (p.is_file() and os.access(str(p), os.X_OK)):
                continue
            r = _sp.run([str(p), "--version"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                return str(p)
        except Exception:
            continue
    return candidates[0] if candidates else "opencode"


OPENCODE_PATH: str = _resolve_opencode_path()

# Fallback model MUST be passed explicitly via -m: the global opencode.json
# default (cliproxy/sensenova-fast) requires an external proxy that may be down.
OPENCODE_DEFAULT_MODEL: str = os.getenv("OPENCODE_MODEL", "sensenova/deepseek-v4-flash").strip()

# Timeout per opencode turn (in seconds, default: 600 = 10 min)
OPENCODE_TIMEOUT: int = int(os.getenv("OPENCODE_TIMEOUT", "600"))

# Provider prefixes identifying an OpenCode model id (agy ids never contain "/")
OPENCODE_PROVIDER_PREFIXES: tuple = ("opencode/", "sensenova/", "minimax", "cliproxy/", "ollama/")


def is_opencode_model(model_id: str) -> bool:
    """Check whether a model id belongs to the local OpenCode backend."""
    if not model_id:
        return False
    mid = model_id.strip().lower()
    return "/" in mid or mid.startswith(OPENCODE_PROVIDER_PREFIXES)

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
# Persistent Memory & State Settings (Hermes-compatible Architecture)
# ---------------------------------------------------------------------------
# Directory where persistent memories (MEMORY.md, USER.md) are stored.
# Defaults to ~/.hermes/memories if present to share memory with Hermes Agent.
_hermes_mem_dir = Path.home() / ".hermes" / "memories"
_default_mem_dir = _hermes_mem_dir if _hermes_mem_dir.exists() else Path.home() / ".gemini" / "tg_bot_memories"
MEMORY_DIR: Path = Path(os.getenv("MEMORY_DIR", str(_default_mem_dir)))
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

# State persistence file to guarantee session & conversation continuity across restarts/disconnects
STATE_FILE: Path = Path(os.getenv("STATE_FILE", str(Path.home() / ".gemini" / "tg_bot_state.json")))
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Auth Helper
# ---------------------------------------------------------------------------
def is_authorized(user_id: int) -> bool:
    """Check if the user is in the allowed user set. Strict verification: must be explicitly bound."""
    if not ALLOWED_USER_IDS:
        return False
    return user_id in ALLOWED_USER_IDS


# Granting a policy permanent exemption from staleness cleanup is a heavier act
# than ordinary bot use, so it has its own allowlist and NEVER inherits from
# ALLOWED_USER_IDS — two permission domains that share a default are one domain.
# Unset, empty, or malformed all resolve to the empty set: /pin and /unpin are
# then refused for everyone. A half-parsed allowlist is the dangerous outcome,
# so one bad entry voids the whole value rather than leaving the valid part.
POLICY_ADMIN_IDS: set[int] = set()
POLICY_ADMIN_CONFIG_ERROR: str = ""

_raw_admins = os.getenv("POLICY_ADMIN_IDS", "").strip()
if not _raw_admins:
    POLICY_ADMIN_CONFIG_ERROR = "POLICY_ADMIN_IDS 未設定"
else:
    _parsed: set[int] = set()
    for _tok in _raw_admins.replace(";", ",").split(","):
        _tok = _tok.strip()
        if not _tok:
            continue
        if not _tok.lstrip("-").isdigit():
            POLICY_ADMIN_CONFIG_ERROR = f"POLICY_ADMIN_IDS 含無效項目 {_tok!r}"
            _parsed = set()
            break
        _parsed.add(int(_tok))
    else:
        if not _parsed:
            POLICY_ADMIN_CONFIG_ERROR = "POLICY_ADMIN_IDS 設定咗但解析唔到任何 ID"
    POLICY_ADMIN_IDS = _parsed


def is_policy_admin(user_id: int) -> bool:
    """Only an explicitly bound admin may pin or unpin a policy."""
    if not POLICY_ADMIN_IDS:
        return False
    return user_id in POLICY_ADMIN_IDS
