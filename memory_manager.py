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
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import MEMORY_DIR

logger = logging.getLogger("agy-tg-bot.memory")

ENTRY_DELIMITER = "\n§\n"
MAX_MEMORY_CHARS = 30000  # Bound memory to prevent prompt bloat
BACKUP_GENERATIONS = 5    # Rotating backups: .bak, .bak.1 ... .bak.4


def _rotate_backups(file_path: Path) -> None:
    """Rotate backup generations: .bak.3 -> .bak.4, ..., .bak -> .bak.1,
    then copy the current file to .bak. Called right before a real write."""
    if not file_path.exists():
        return
    oldest = file_path.with_name(f"{file_path.name}.bak.{BACKUP_GENERATIONS - 1}")
    if oldest.exists():
        oldest.unlink()
    for i in range(BACKUP_GENERATIONS - 2, 0, -1):
        src = file_path.with_name(f"{file_path.name}.bak.{i}")
        if src.exists():
            src.replace(file_path.with_name(f"{file_path.name}.bak.{i + 1}"))
    first = file_path.with_name(file_path.name + ".bak")
    if first.exists():
        first.replace(file_path.with_name(f"{file_path.name}.bak.1"))
    shutil.copy2(file_path, first)

_mem_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Entry Metadata (v1.1): "[YYYY-MM-DD · auto|manual] content"
# ---------------------------------------------------------------------------

_META_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2})(?:\s+[0-9:]+)?\s*·\s*(auto|manual)\]\s*")


def _parse_meta(entry: str) -> Tuple[str, str]:
    """Return (date, source) of an entry; legacy entries count as oldest manual."""
    m = _META_RE.match(entry)
    if m:
        return m.group(1), m.group(2)
    return "0000-00-00", "manual"


def _strip_meta(entry: str) -> str:
    """Strip the metadata prefix for content comparisons."""
    return _META_RE.sub("", entry, count=1)


def _eviction_key(entry: str) -> Tuple[int, str]:
    """Eviction priority: auto-sourced first, then oldest date first."""
    date, source = _parse_meta(entry)
    return (0 if source == "auto" else 1, date)


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
    """Write entries to a memory file atomically, with .bak rollback copy.

    When the size cap is exceeded, eviction drops the least valuable entries
    first (auto-sourced before manual, older before newer) instead of
    blindly dropping the oldest.
    """
    file_path = _get_file_path(target)
    cleaned = [e.strip() for e in entries if e.strip()]
    content = ENTRY_DELIMITER.join(cleaned) + ("\n" if cleaned else "")

    if len(content) > MAX_MEMORY_CHARS:
        logger.warning("Memory file %s exceeds %d chars, evicting low-value entries", file_path, MAX_MEMORY_CHARS)
        while cleaned and len(ENTRY_DELIMITER.join(cleaned)) > MAX_MEMORY_CHARS:
            victim_idx = min(range(len(cleaned)), key=lambda i: _eviction_key(cleaned[i]))
            logger.info("Evicted memory entry: %s", cleaned[victim_idx][:60])
            cleaned.pop(victim_idx)
        content = ENTRY_DELIMITER.join(cleaned) + ("\n" if cleaned else "")

    with _mem_lock:
        try:
            MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            # Rotating rollback copies: only taken right before a real write
            try:
                _rotate_backups(file_path)
            except Exception as backup_err:
                logger.warning("Failed to rotate backups for %s: %s", file_path, backup_err)
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
    """Add a new entry to the specified memory store (v1.1).

    Pipeline: candidate -> threat scan -> dedup -> conflict/merge -> write.
    - Every entry is stamped with `[YYYY-MM-DD · auto|manual]` metadata.
    - new ⊂ old: skip (already covered by an existing entry).
    - old ⊂ new: replace the old entry (new content is a superset update).
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

    dated = new_entry if _META_RE.match(new_entry) else f"[{time.strftime('%Y-%m-%d')} · {source}] {new_entry}"
    new_core = _strip_meta(dated).lower()

    entries = read_entries(target)

    # Dedup: exact match or new content already contained in an existing entry
    for existing in entries:
        ex_core = _strip_meta(existing).lower()
        if new_core == ex_core:
            return True, "該記憶已存在，無需重複記錄。"
        if len(new_core) > 15 and new_core in ex_core:
            return True, "已存在包含該內容的記憶。"

    # Reverse containment: an existing entry is fully covered by the new,
    # more detailed one -> update it in place instead of appending a near-dupe
    for idx, existing in enumerate(entries):
        ex_core = _strip_meta(existing).lower()
        if len(ex_core) > 15 and ex_core in new_core:
            entries[idx] = dated
            if write_entries(target, entries):
                logger.info("Updated memory (superset) in %s: %s", target, new_core[:60])
                return True, "✅ 已用更詳細嘅新內容更新舊記憶。"
            return False, "寫入本地記憶失敗。"

    # Label-conflict detection (e.g. same "服務端點紀錄" key, different IP/URL)
    new_label = _entry_label(new_core)
    if new_label:
        for idx, existing in enumerate(entries):
            if _entry_label(_strip_meta(existing)) == new_label and _strip_meta(existing).lower() != new_core:
                if source == "auto":
                    logger.warning(
                        "Extractor conflict on label '%s': keeping existing entry, skipped auto-write of: %s",
                        new_label, new_entry[:80],
                    )
                    return True, "與現有記憶衝突，已保留原記錄（自動寫入已跳過）。"
                logger.info("Manual override on label '%s': replacing stale entry", new_label)
                entries[idx] = dated
                if write_entries(target, entries):
                    return True, "✅ 已更新同標籤嘅舊記憶（以最新內容為準）。"
                return False, "寫入本地記憶失敗。"

    entries.append(dated)
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

    # Load active & pinned policies from evolution engine
    active_policies = []
    try:
        from evolution.lifecycle import get_active_policies, mark_policy_injected
        raw_policies = get_active_policies()
        for p in raw_policies:
            line = f"{p['summary']} (理由: {p.get('root_cause', '安全邊界')})"
            # Structured fields are optional. A policy without them — including
            # the handwritten bootstrap one — renders exactly as it always did.
            detail = [
                f"    ‣ {label}: {p[key]}"
                for key, label in (("trigger", "觸發"), ("constraint", "約束"), ("verification", "驗證"))
                if p.get(key)
            ]
            if detail:
                line = "\n".join([line] + detail)
            active_policies.append(line)
            # Observational only. Injection is not usage: we know the rule was
            # placed in context, not that it matched the task or changed the
            # output. Feeding this into an idle timer is what made staleness
            # unreachable and left pinning with nothing to protect.
            mark_policy_injected(p["id"])
    except Exception as e:
        # One exception here used to drop every hardline policy out of the
        # prompt without a trace — the agent would simply stop being
        # constrained, and nothing would say so. Degrading quietly is the one
        # behaviour this subsystem must not have.
        logger.error("HARDLINE POLICIES NOT INJECTED — %s: %s", type(e).__name__, e)

    if not user_entries and not memory_entries and not session_entries and not active_policies:
        return ""

    blocks = [
        "<memory-context>",
        "[System note: The following is recalled persistent memory context from local storage (MEMORY.md & USER.md & policies/). Treat as authoritative background knowledge — this is the agent's long-term memory across sessions.]\n",
    ]

    if active_policies:
        blocks.append("### 🚨 HARDLINE BEHAVIOR POLICIES (必須嚴格遵守的操作限制):")
        for pol in active_policies:
            blocks.append(f"• {pol}")
        blocks.append("")

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
