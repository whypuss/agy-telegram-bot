"""
Policy store access.

There is no automatic lifecycle. The only signal available is that a policy was
injected into a prompt, and that is not evidence it applied to the task or
changed the output:

    injected  !=  matched  !=  affected_output

Ageing rules on that signal was unsound, so the stale/archive machinery — and
the `pinned` flag whose sole effect was exempting a policy from it — has been
removed rather than left disabled. A feature that still ships its API, its
directories and its status values is not switched off, it is merely quiet.

Policies are managed by hand: `status: active` is injected, anything else is
not, and deleting a rule means removing its file.
"""
import json
import logging
import time
from pathlib import Path
from typing import Dict, Any, List

from config import MEMORY_DIR

logger = logging.getLogger("agy-tg-bot.evolution.lifecycle")

POLICIES_DIR = MEMORY_DIR / "policies"
POLICIES_DIR.mkdir(parents=True, exist_ok=True)


def get_active_policies() -> List[Dict[str, Any]]:
    """Every policy currently in force, for injection into a fresh conversation."""
    if not POLICIES_DIR.exists():
        return []
    active = []
    for f in sorted(POLICIES_DIR.glob("*.json")):
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

    Writes `last_injected_at` and nothing else. No code reads it for a decision;
    it exists so that if a real relevance signal is ever designed, there is a
    baseline to compare against.
    """
    p_path = POLICIES_DIR / f"{policy_id}.json"
    if not p_path.exists():
        return
    try:
        d = json.loads(p_path.read_text(encoding="utf-8"))
        d["last_injected_at"] = int(time.time())
        p_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def set_policy_status(policy_id: str, status: str) -> tuple:
    """Enable or disable a policy by hand. The only lifecycle there is."""
    if status not in ("active", "disabled"):
        return False, f"❌ 無效狀態 `{status}`,只接受 active / disabled。"
    p_path = POLICIES_DIR / f"{policy_id}.json"
    if not p_path.exists():
        return False, f"❌ 找不到 policy `{policy_id}`"
    try:
        d = json.loads(p_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"❌ 讀取 policy 失敗: {type(e).__name__}: {e}"

    was = d.get("status", "active")
    if was == status:
        return True, f"ℹ️ `{policy_id}` 已經係 `{status}`,未作改動。"
    d["status"] = status
    d["last_updated"] = int(time.time())
    p_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Policy %s: %s -> %s", policy_id, was, status)
    return True, f"✅ `{policy_id}`: `{was}` → `{status}`"
