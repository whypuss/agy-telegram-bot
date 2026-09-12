"""
Deterministic Lifecycle Engine for Policies and Skills.
Zero LLM hallucination. Pinned entries are strictly immune.
Never deletes; only archives.
"""
import time
import json
import shutil
import logging
from pathlib import Path
from typing import Dict, Any, List

from config import MEMORY_DIR

logger = logging.getLogger("agy-tg-bot.evolution.lifecycle")

POLICIES_DIR = MEMORY_DIR / "policies"
ARCHIVE_DIR = MEMORY_DIR / "policies/.archive"
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

STALE_DAYS = 30
ARCHIVE_DAYS = 90

# Automatic ageing is off.
#
# The only signal the system has is that a policy was injected into context —
# not that it matched the task, and not that it changed the output:
#
#     injected  !=  matched  !=  affected_output
#
# Treating injection as usage made `last_used_at` refresh on every conversation,
# so staleness could never fire and `pinned` protected nothing. Rather than keep
# a lifecycle that reports work it is not doing, it stays disabled until there is
# a real relevance signal to age on. Policies are managed by hand meanwhile:
# active / disabled, and deletion is a person removing the file.
POLICY_AUTO_LIFECYCLE_ENABLED = False

def get_active_policies() -> List[Dict[str, Any]]:
    """Retrieve all currently active and pinned policies for runtime injection."""
    if not POLICIES_DIR.exists():
        return []
    active = []
    now = int(time.time())
    for f in POLICIES_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("status") == "active":
                active.append(d)
        except Exception as e:
            # An unreadable policy file means that rule silently stops being
            # enforced. Skipping it is the right recovery; doing so quietly is
            # not — this is the failure the whole subsystem exists to prevent.
            logger.error("POLICY NOT LOADED, rule is not in effect: %s — %s: %s",
                         f.name, type(e).__name__, e)
    return active

def mark_policy_injected(policy_id: str) -> None:
    """Record that a policy was placed into a prompt. Observational only.

    Deliberately writes `last_injected_at`, never `last_used_at`: being loaded
    into context is not evidence the rule was relevant. Nothing reads this for
    lifecycle decisions — it exists so that, if a usage signal is designed
    later, there is a baseline to compare against.
    """
    p_path = POLICIES_DIR / f"{policy_id}.json"
    if p_path.exists():
        try:
            d = json.loads(p_path.read_text(encoding="utf-8"))
            d["last_injected_at"] = int(time.time())
            p_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

def run_lifecycle_pass() -> Dict[str, int]:
    """
    Periodic deterministic maintenance pass.
    Moves idle policies through: active -> stale -> archived.
    Pinned policies are 100% exempt.
    """
    stats = {"stale": 0, "archived": 0, "skipped_pinned": 0, "disabled": 0}
    now = int(time.time())

    if not POLICY_AUTO_LIFECYCLE_ENABLED:
        stats["disabled"] = 1
        return stats

    for f in POLICIES_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            # Pinned security hardlines are completely exempt
            if d.get("pinned", False):
                stats["skipped_pinned"] += 1
                continue

            last_used = d.get("last_used_at", d.get("created_at", now))
            idle_days = (now - last_used) / 86400

            # 90+ days -> Archive (move to .archive/)
            if idle_days >= ARCHIVE_DAYS:
                dest = ARCHIVE_DIR / f.name
                shutil.move(str(f), str(dest))
                d["status"] = "archived"
                d["archived_at"] = now
                dest.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
                stats["archived"] += 1
                logger.info("📦 [Lifecycle Archive] %s moved to .archive/", f.name)

            # 30+ days -> Mark Stale
            elif idle_days >= STALE_DAYS and d.get("status") != "stale":
                d["status"] = "stale"
                f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
                stats["stale"] += 1
                logger.info("⏳ [Lifecycle Stale] %s marked as stale", f.name)

        except Exception as e:
            logger.warning("Lifecycle pass error on %s: %s", f, e)

    return stats
