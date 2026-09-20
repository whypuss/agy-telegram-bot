"""
Standing behavioural rules, injected into the start of every new conversation.

Rules are written by hand as JSON files in MEMORY_DIR/policies/. There is no
pipeline that generates them: an LLM-driven evaluator, an evidence gate, a
proposal queue and an approval flow all existed here and produced exactly zero
rules across their entire lifetime, so they were removed along with the
`evolution` package that named them.

What remains is the part that was doing real work — reading the rules and
putting them in the prompt.
"""
import json
import logging
import re
import time
from typing import Any, Dict, List, Tuple

from config import MEMORY_DIR

logger = logging.getLogger("agy-tg-bot.policy")

POLICIES_DIR = MEMORY_DIR / "policies"
POLICIES_DIR.mkdir(parents=True, exist_ok=True)


def get_active_policies() -> List[Dict[str, Any]]:
    """Every rule currently in force, for injection into a fresh conversation."""
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
            # not — a constraint that disappears without a word is the failure
            # this module exists to prevent.
            logger.error("POLICY NOT LOADED, rule is not in effect: %s — %s: %s",
                         f.name, type(e).__name__, e)
    return active


def mark_policy_injected(policy_id: str) -> None:
    """Record that a rule was placed into a prompt. Observational only.

    Writes `last_injected_at` and nothing else. No code reads it for a decision.
    Injection is not usage: it says the rule was in context, not that it applied
    to the task or changed the output.
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


def set_policy_status(policy_id: str, status: str) -> Tuple[bool, str]:
    """Enable or disable a rule. The only lifecycle there is."""
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


def save_policy(
    policy_id: str,
    summary: str,
    root_cause: str = "",
    trigger: str = "",
    constraint: str = "",
    verification: str = "",
    evidence: str = "",
) -> Tuple[bool, str]:
    """Save or update a hardline policy file in POLICIES_DIR.

    Normalizes policy_id (prefix with 'rule_', lowercase, valid characters).
    """
    clean_id = policy_id.strip().lower()
    clean_id = re.sub(r'[^a-z0-9_-]', '_', clean_id)
    if not clean_id.startswith("rule_"):
        clean_id = f"rule_{clean_id}"
    clean_id = re.sub(r'_+', '_', clean_id).strip('_')

    if not summary.strip():
        return False, "❌ 規則摘要 (summary) 不可為空。"

    POLICIES_DIR.mkdir(parents=True, exist_ok=True)
    p_path = POLICIES_DIR / f"{clean_id}.json"
    now = int(time.time())

    existing = {}
    if p_path.exists():
        try:
            existing = json.loads(p_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    record = {
        "id": clean_id,
        "summary": summary.strip(),
        "root_cause": root_cause.strip() or existing.get("root_cause", "安全邊界與行為約束"),
        "evidence": evidence.strip() or existing.get("evidence", "Derived from /learn"),
        "version": existing.get("version", 0) + 1,
        "created_at": existing.get("created_at", now),
        "last_updated": now,
        "status": "active",
    }
    if trigger.strip():
        record["trigger"] = trigger.strip()
    elif "trigger" in existing:
        record["trigger"] = existing["trigger"]

    if constraint.strip():
        record["constraint"] = constraint.strip()
    elif "constraint" in existing:
        record["constraint"] = existing["constraint"]

    if verification.strip():
        record["verification"] = verification.strip()
    elif "verification" in existing:
        record["verification"] = existing["verification"]

    try:
        p_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Saved policy %s: %s", clean_id, summary[:60])
        return True, f"✅ 已成功儲存規則 `{clean_id}`"
    except Exception as e:
        logger.error("Failed saving policy %s: %s", clean_id, e)
        return False, f"❌ 儲存規則失敗: {e}"

