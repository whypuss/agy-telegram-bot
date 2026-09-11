"""
Persistent Curated Memory System (Hermes Agent Architecture Reference).

Features:
- Dual-Store Local File Storage:
    - MEMORY.md: Agent facts, environment data, server IPs, ports, deployment rules.
    - USER.md: User preferences, habits, instructions, communication style.
- Delimited entries using '§' (Hermes standard) with atomic persistence.
- Injection threat scanning and invisible character scrubbing.
- Continuous runtime & conversation listener (extracts memories from live chats).
- Safe prompt injection via fenced <memory-context> blocks.
"""

import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import MEMORY_DIR

logger = logging.getLogger("agy-tg-bot.memory")

ENTRY_DELIMITER = "\n§\n"
MAX_MEMORY_CHARS = 30000  # Bound memory to prevent prompt bloat

_mem_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Threat & Injection Protection (from Hermes Agent)
# ---------------------------------------------------------------------------

_INVISIBLE_CHARS = {
    '\u200b', '\u200c', '\u200d', '\u2060', '\ufeff',
    '\u202a', '\u202b', '\u202c', '\u202d', '\u202e',
}

_MEMORY_THREAT_PATTERNS = [
    (r'ignore\s+(previous|all|above|prior)\s+instructions', "prompt_injection"),
    (r'you\s+are\s+now\s+', "role_hijack"),
    (r'do\s+not\s+tell\s+the\s+user', "deception_hide"),
    (r'system\s+prompt\s+override', "sys_prompt_override"),
    (r'disregard\s+(your|all|any)\s+(instructions|rules|guidelines)', "disregard_rules"),
]


def scan_memory_content(content: str) -> Optional[str]:
    """Scan memory content for injection/exfil patterns. Returns error message if blocked."""
    for char in _INVISIBLE_CHARS:
        if char in content:
            return f"Blocked: contains invisible unicode character U+{ord(char):04X}."
    for pattern, pid in _MEMORY_THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return f"Blocked: matches suspicious security pattern '{pid}'."
    return None


# ---------------------------------------------------------------------------
# File Read & Write Helpers
# ---------------------------------------------------------------------------

def _get_file_path(target: str) -> Path:
    target_lower = target.lower().strip()
    if target_lower in ("user", "user.md", "preference", "pref"):
        return MEMORY_DIR / "USER.md"
    if target_lower in ("sessions", "session", "sessions.md"):
        return MEMORY_DIR / "SESSIONS.md"
    return MEMORY_DIR / "MEMORY.md"


def read_entries(target: str) -> List[str]:
    """Read entries from a memory file, split by delimiter."""
    file_path = _get_file_path(target)
    if not file_path.exists():
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw = f.read()
        entries = [e.strip() for e in raw.split("§") if e.strip()]
        return entries
    except Exception as e:
        logger.error("Failed to read memory file %s: %s", file_path, e)
        return []


def write_entries(target: str, entries: List[str]) -> bool:
    """Write entries to a memory file atomically."""
    file_path = _get_file_path(target)
    cleaned = [e.strip() for e in entries if e.strip()]
    content = ENTRY_DELIMITER.join(cleaned) + ("\n" if cleaned else "")

    if len(content) > MAX_MEMORY_CHARS:
        logger.warning("Memory file %s exceeds %d chars, truncating oldest entries", file_path, MAX_MEMORY_CHARS)
        while cleaned and len(ENTRY_DELIMITER.join(cleaned)) > MAX_MEMORY_CHARS:
            cleaned.pop(0)
        content = ENTRY_DELIMITER.join(cleaned) + ("\n" if cleaned else "")

    with _mem_lock:
        try:
            MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=str(MEMORY_DIR), prefix=f".{file_path.name}_", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, file_path)
            return True
        except Exception as e:
            logger.error("Failed to write memory file %s: %s", file_path, e)
            return False


def _entry_label(entry: str) -> str:
    """Extract the 'label' prefix of an entry (text before the first colon)."""
    parts = re.split(r"[：:]", entry, maxsplit=1)
    if len(parts) == 2 and 2 <= len(parts[0].strip()) <= 30:
        return parts[0].strip().lower()
    return ""


def add_entry(target: str, new_entry: str, source: str = "manual") -> Tuple[bool, str]:
    """Add a new entry to the specified memory store.

    Validation gate (candidate -> validate -> dedup -> merge):
    - Threat scan and exact/containment dedup always apply.
    - Label conflict (same label prefix, different value): automatic extractor
      writes are SKIPPED (a model guess must never overwrite curated facts);
      manual writes replace the stale entry (user authority wins).
    """
    new_entry = new_entry.strip()
    if not new_entry:
        return False, "記憶內容不可為空。"

    err = scan_memory_content(new_entry)
    if err:
        return False, err

    entries = read_entries(target)
    # Deduplication check
    for existing in entries:
        if new_entry.lower() == existing.lower():
            return True, "該記憶已存在，無需重複記錄。"
        if len(new_entry) > 15 and new_entry.lower() in existing.lower():
            return True, "已存在包含該內容的記憶。"

    # Label-conflict detection (e.g. same "服務端點紀錄" key, different IP/URL)
    new_label = _entry_label(new_entry)
    if new_label:
        for idx, existing in enumerate(entries):
            if _entry_label(existing) == new_label and existing.lower() != new_entry.lower():
                if source == "auto":
                    logger.warning(
                        "Extractor conflict on label '%s': keeping existing entry, skipped auto-write of: %s",
                        new_label, new_entry[:80],
                    )
                    return True, "與現有記憶衝突，已保留原記錄（自動寫入已跳過）。"
                logger.info("Manual override on label '%s': replacing stale entry", new_label)
                entries[idx] = new_entry
                if write_entries(target, entries):
                    return True, "✅ 已更新同標籤嘅舊記憶（以最新內容為準）。"
                return False, "寫入本地記憶失敗。"

    entries.append(new_entry)
    if write_entries(target, entries):
        logger.info("Saved new memory to %s: %s", target, new_entry[:60])
        return True, "✅ 記憶已成功儲存至本機磁碟。"
    return False, "寫入本地記憶失敗。"


def add_session_summary(summary: str) -> bool:
    """Append a /compact session summary to SESSIONS.md (keeps last 5).

    Session summaries are short-term continuity aids, kept separate from the
    curated long-term facts in MEMORY.md.
    """
    import time as _time

    summary = summary.strip()
    if not summary:
        return False
    entries = read_entries("sessions")
    entries.append(f"[{_time.strftime('%Y-%m-%d %H:%M')}] {summary[:3000]}")
    entries = entries[-5:]
    return write_entries("sessions", entries)


def remove_entry(target: str, pattern: str) -> Tuple[bool, str]:
    """Remove entries matching the pattern substring."""
    pattern = pattern.strip().lower()
    if not pattern:
        return False, "請指定欲刪除的關鍵字。"

    entries = read_entries(target)
    initial_count = len(entries)
    remaining = [e for e in entries if pattern not in e.lower()]

    if len(remaining) == initial_count:
        return False, f"未找到包含「{pattern}」的記憶條目。"

    if write_entries(target, remaining):
        removed_count = initial_count - len(remaining)
        return True, f"✅ 已成功刪除 {removed_count} 條相關記憶。"
    return False, "更新本地記憶檔案失敗。"


def search_memories(keyword: str) -> List[Dict[str, str]]:
    """Search for keyword across both USER.md and MEMORY.md."""
    kw = keyword.strip().lower()
    results = []

    for target in ("user", "memory"):
        entries = read_entries(target)
        for e in entries:
            if kw in e.lower():
                results.append({"store": target.upper(), "content": e})

    return results


# ---------------------------------------------------------------------------
# Prompt Context Injection (Hermes Frozen Snapshot Pattern)
# ---------------------------------------------------------------------------

def build_memory_context() -> str:
    """
    Build a safe, fenced memory context string for injecting into the Agent prompt.
    Combines both USER.md (preferences) and MEMORY.md (system facts).
    """
    user_entries = read_entries("user")
    memory_entries = read_entries("memory")
    session_entries = read_entries("sessions")

    if not user_entries and not memory_entries and not session_entries:
        return ""

    blocks = [
        "<memory-context>",
        "[System note: The following is recalled persistent memory context from local storage (MEMORY.md & USER.md). Treat as authoritative background knowledge — this is the agent's long-term memory across sessions.]\n",
    ]

    if user_entries:
        blocks.append("### 👤 User Preferences & Interaction Guidelines (USER.md):")
        for e in user_entries:
            blocks.append(f"• {e}")
        blocks.append("")

    if memory_entries:
        blocks.append("### 🧠 System & Environment Facts (MEMORY.md):")
        for e in memory_entries:
            blocks.append(f"• {e}")
        blocks.append("")

    if session_entries:
        blocks.append("### 📂 Previous Session Summary (SESSIONS.md):")
        blocks.append(session_entries[-1])
        blocks.append("")

    blocks.append("</memory-context>\n")
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# Continual Listening & Runtime Memory Extractor
# ---------------------------------------------------------------------------

_USER_PREF_KEYWORDS = [
    "記住", "請記住", "以後都", "務必", "偏好", "我喜歡", "不要再", "不要每次", "記一下",
    "remember to", "always use", "never use", "my preference is", "i prefer"
]

_SYS_FACT_PATTERNS = [
    # IPs and Ports
    r'(?:https?://)?(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?::[0-9]{2,5})?',
    # Domain configs
    r'(?:[a-zA-Z0-9-]+\.)+(?:com|cc|org|net|cn|me|io|xyz)(?::[0-9]{2,5})?',
]


def monitor_and_extract(user_prompt: str, agent_response: str) -> None:
    """
    Background worker that continuously inspects conversational interactions
    and execution outputs to extract durable facts and user preferences.
    """
    if not user_prompt:
        return

    # 1. User Intent Detection (Explicit Preferences & Rules)
    prompt_clean = user_prompt.strip()
    for kw in _USER_PREF_KEYWORDS:
        if kw in prompt_clean.lower():
            # Extract preference statement
            idx = prompt_clean.lower().find(kw)
            extracted = prompt_clean[idx:].strip()
            # Clean up leading prefix
            extracted = re.sub(r'^(記住|請記住|以後都|記一下|remember\s+to|remember)\s*[,:：，]?\s*', '', extracted, flags=re.IGNORECASE)
            if len(extracted) >= 4:
                logger.info("Continual Listener: detected user preference -> saving to USER.md")
                add_entry("user", extracted[:300], source="auto")
                break

    # 2. Execution Output Fact Detection (Server, Deployments, Credentials)
    if agent_response and len(agent_response) > 50:
        # Detect endpoints, credentials or critical deploy rules
        deploy_matches = re.findall(r'(?:端點|服務|部署|上線|port|網址|URL|API|伺服器)[：:\s]+(https?://[^\s`"\'\)]+|[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}:[0-9]+)', agent_response, re.IGNORECASE)
        for dep in deploy_matches[:2]:
            snippet = f"服務端點紀錄：{dep.strip()}"
            add_entry("memory", snippet, source="auto")
