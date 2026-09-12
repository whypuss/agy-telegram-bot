"""
Deterministic Policy Gate & Dispatcher.
Routes low-risk entries (profile/facts) to memory files automatically,
and gates high-risk entries (policies/skills) into Proposals for Telegram approval.
"""
import re
import json
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from config import MEMORY_DIR
from memory_manager import add_entry
from evolution.schema import EvolutionCandidate, Proposal, VersionedPolicy
from evolution.evidence import check_evidence

logger = logging.getLogger("agy-tg-bot.evolution.gate")

# Candidate types that become standing rules, and so must clear the evidence gate.
_PROMOTION_TYPES = ("policy_proposal", "skill_patch")

PROPOSALS_DIR = MEMORY_DIR / "proposals"
POLICIES_DIR = MEMORY_DIR / "policies"
SKILLS_DIR = Path.home() / ".gemini/antigravity-cli/skills"

PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
POLICIES_DIR.mkdir(parents=True, exist_ok=True)
SKILLS_DIR.mkdir(parents=True, exist_ok=True)

_TEMPORARY_PATTERNS = [
    r"今[天日晚]", r"這[次回條]", r"暫時", r"剛剛", r"先行?", r"測試一下",
    r"\btoday\b", r"\bfor now\b", r"\bthis time\b", r"\bjust now\b"
]

CONFIDENCE_THRESHOLD = 0.85

def validate_candidate(cand: EvolutionCandidate) -> bool:
    """Deterministic validation without LLM hallucination risk."""
    if cand.scope != "permanent":
        logger.info("❌ [Gate] Dropping temporary scope candidate: %s", cand.summary)
        return False

    if cand.confidence < CONFIDENCE_THRESHOLD:
        logger.info("❌ [Gate] Dropping low-confidence candidate (%0.2f < %0.2f): %s",
                    cand.confidence, CONFIDENCE_THRESHOLD, cand.summary)
        return False

    if len(cand.summary) < 4 or len(cand.summary) > 200:
        logger.info("❌ [Gate] Invalid summary length: %s", cand.summary)
        return False

    for pat in _TEMPORARY_PATTERNS:
        if re.search(pat, cand.summary, re.IGNORECASE):
            logger.info("❌ [Gate] Matched temporary word pattern '%s': %s", pat, cand.summary)
            return False

    # Evidence gate — promotion only. A candidate that would become a standing
    # rule must carry a verification that exercised the behavior it governs.
    if cand.candidate_type in _PROMOTION_TYPES:
        verdict = check_evidence(cand.evidence, cand.affected_behavior)
        if not verdict.ok:
            logger.info("❌ [Gate/Evidence] %s — %s | candidate: %s",
                        verdict.code, verdict.reason, cand.summary)
            return False
        logger.info("🔬 [Gate/Evidence] passed: %s", verdict.reason)

    return True

async def dispatch_candidate(
    cand: EvolutionCandidate,
    bot: Any = None,
    notify_chat_id: Optional[int] = None
) -> Optional[str]:
    """
    Safely route validated candidates.
    Returns proposal_id if gated for human review, None if automatically committed or dropped.
    """
    if not validate_candidate(cand):
        return None

    # 1. Low-risk entries: Auto-commit to user profile or system facts
    if cand.candidate_type == "user_profile":
        add_entry("user", cand.summary, source="auto")
        logger.info("📝 [Gate -> USER.md] %s", cand.summary)
        return None

    if cand.candidate_type == "system_facts":
        add_entry("memory", cand.summary, source="auto")
        logger.info("📝 [Gate -> MEMORY.md] %s", cand.summary)
        return None

    # 2. High-risk entries: Policy proposal or Skill patch (HOLD for Telegram approval)
    #
    # Pinning is never inherited from the candidate. The evaluator is a model and
    # can ask for `pinned: true`; honouring that would let one misjudgement mint a
    # rule that is permanently exempt from staleness cleanup. Clearing the gate
    # earns a candidate `active`, not `pinned` — pinning stays a human act via
    # pin_policy().
    if cand.pinned:
        logger.info("📌 [Gate] Ignoring evaluator-requested pinning for %s — "
                    "pinning is a human action, not a pipeline outcome.", cand.rule_id)

    prop_id = f"p_{int(time.time())}_{cand.rule_id[:16]}"
    proposal = Proposal(
        id=prop_id,
        proposal_type=cand.candidate_type,
        summary=cand.summary,
        root_cause=cand.root_cause,
        evidence=cand.evidence or "",
        affected_behavior=cand.affected_behavior or "",
        pinned=False,
        skill_class=cand.skill_class,
        confidence=cand.confidence,
        created_at=int(time.time()),
        status="pending_approval"
    )

    prop_path = PROPOSALS_DIR / f"{prop_id}.json"
    prop_path.write_text(json.dumps(proposal.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("🛡️ [Gate -> Proposal] Created %s: %s (pinned=%s)", prop_id, cand.summary, cand.pinned)

    # 3. Notify user via Telegram if bot and chat_id are available
    if bot and notify_chat_id:
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            type_label = "🚨 行為安全約束 (Policy)" if cand.candidate_type == "policy_proposal" else "🛠️ 類級別技能補丁 (Skill)"

            text = (
                f"💡 *【自我進化提案待審批】*\n"
                f"• *類型*: {type_label}\n"
                f"• *規則內容*: `{cand.summary}`\n"
                f"• *根因分析*: {cand.root_cause}\n"
            )
            if cand.affected_behavior:
                text += f"• *受影響行為*: {cand.affected_behavior[:100]}\n"
            if cand.evidence:
                text += f"• *驗證證據*: _{cand.evidence[:160]}_\n"
            text += "• _批准後為 active,非 pinned;要 pin 需另行 /pin_\n"

            text += f"\n請確認是否批准生效：\n/approve {prop_id} 或點擊下方按鈕"

            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ 批准生效", callback_data=f"evo:app:{prop_id}"),
                    InlineKeyboardButton("❌ 拒絕忽略", callback_data=f"evo:rej:{prop_id}")
                ]
            ])
            await bot.send_message(chat_id=notify_chat_id, text=text, reply_markup=kb, parse_mode="Markdown")
        except Exception as te:
            logger.warning("Failed to send proposal notification to Telegram: %s", te)

    return prop_id

def get_proposal(proposal_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a proposal by ID."""
    prop_path = PROPOSALS_DIR / f"{proposal_id}.json"
    if not prop_path.exists():
        return None
    try:
        return json.loads(prop_path.read_text(encoding="utf-8"))
    except Exception:
        return None

def approve_proposal(proposal_id: str, approved_by: Optional[int] = None) -> Tuple[bool, str]:
    """Approve a proposal and compile it into an active Policy or Skill."""
    prop_path = PROPOSALS_DIR / f"{proposal_id}.json"
    if not prop_path.exists():
        return False, f"❌ 找不到提案 `{proposal_id}`"

    try:
        data = json.loads(prop_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"❌ 讀取提案失敗: {e}"

    if data.get("status") != "pending_approval":
        return False, f"⚠️ 提案狀態非待審批（目前: {data.get('status')}）"

    prop_type = data.get("proposal_type")
    summary = data.get("summary")
    now = int(time.time())

    # Defence in depth. The gate already ran at dispatch; re-checking here means a
    # proposal hand-edited on disk, or written before the gate existed, cannot be
    # approved into a standing rule. The compiler's job is to format what was
    # validated — never to supply, infer, or improve the evidence itself.
    if prop_type in _PROMOTION_TYPES:
        verdict = check_evidence(data.get("evidence"), data.get("affected_behavior"))
        if not verdict.ok:
            logger.info("❌ [Compiler/Evidence] refused %s: %s", proposal_id, verdict.reason)
            return False, (f"❌ 提案 `{proposal_id}` 未能通過證據門檻：{verdict.reason}\n"
                           f"（`{verdict.code}`）編譯器唔會代為補作證據。")

    # 1. Compile Policy
    if prop_type == "policy_proposal":
        rule_id = data.get("id", proposal_id).replace("p_", "rule_")
        policy_path = POLICIES_DIR / f"{rule_id}.json"
        version = 1
        created_at = now
        if policy_path.exists():
            try:
                old = json.loads(policy_path.read_text(encoding="utf-8"))
                version = old.get("version", 1) + 1
                created_at = old.get("created_at", now)
            except: pass

        # Evidence is carried through byte-for-byte. A re-compiled policy keeps
        # whatever pinning a human already granted it, but approval never adds it.
        was_pinned = False
        if policy_path.exists():
            try:
                was_pinned = bool(json.loads(policy_path.read_text(encoding="utf-8")).get("pinned", False))
            except Exception:
                pass

        policy = VersionedPolicy(
            id=rule_id,
            summary=summary,
            root_cause=data.get("root_cause", ""),
            evidence=data.get("evidence", ""),
            affected_behavior=data.get("affected_behavior", ""),
            pinned=was_pinned,
            version=version,
            created_at=created_at,
            last_updated=now,
            last_used_at=now,
            status="active"
        )
        policy_path.write_text(json.dumps(policy.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("✅ [Proposal Approved] Compiled into active Policy: %s v%d", rule_id, version)

    # 2. Compile Skill Patch
    elif prop_type == "skill_patch":
        sclass = data.get("skill_class") or "general-operations"
        target_skill_dir = SKILLS_DIR / sclass
        target_skill_dir.mkdir(parents=True, exist_ok=True)
        refs_dir = target_skill_dir / "references"
        refs_dir.mkdir(parents=True, exist_ok=True)
        
        # Append to references/pitfalls.md
        pitfall_file = refs_dir / "pitfalls.md"
        line = f"- [{time.strftime('%Y-%m-%d')}] {summary} (Reason: {data.get('root_cause')})\n"
        with open(pitfall_file, "a", encoding="utf-8") as pf:
            pf.write(line)
        logger.info("✅ [Proposal Approved] Appended to %s/references/pitfalls.md", sclass)

    # Update proposal record
    data["status"] = "approved"
    data["resolved_at"] = now
    data["approved_by"] = approved_by
    prop_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True, f"✅ 提案 `{proposal_id}` 已批准並編譯生效！"

def reject_proposal(proposal_id: str, rejected_by: Optional[int] = None) -> Tuple[bool, str]:
    """Reject a proposal."""
    prop_path = PROPOSALS_DIR / f"{proposal_id}.json"
    if not prop_path.exists():
        return False, f"❌ 找不到提案 `{proposal_id}`"

    try:
        data = json.loads(prop_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"❌ 讀取提案失敗: {e}"

    data["status"] = "rejected"
    data["resolved_at"] = int(time.time())
    data["approved_by"] = rejected_by
    prop_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("❌ [Proposal Rejected] %s", proposal_id)
    return True, f"🚫 提案 `{proposal_id}` 已駁回。"

# A rule id addresses one file inside POLICIES_DIR and nothing else. Telegram
# text reaches this function, so the id is constrained rather than trusted.
_RULE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Fields pin/unpin is allowed to touch. Everything else — summary, root_cause,
# evidence, version, created_at, provenance — is immutable to this operation.
_PIN_MUTABLE = {"pinned", "pinned_by", "pinned_at", "last_updated"}


def set_policy_pinned(policy_id: str, pinned: bool, actor: Optional[int] = None) -> Tuple[bool, str]:
    """Pin or unpin an existing active policy. Human action only.

    The evidence gate deliberately never pins, so this is the sole path to a
    staleness-immune rule — and it requires a person to take it. The caller
    supplies only a verified identity, an exact rule id, and the target state;
    every read, check and write happens here.
    """
    policy_id = (policy_id or "").strip()
    if not _RULE_ID_RE.match(policy_id) or policy_id != Path(policy_id).name:
        return False, f"❌ 無效 rule ID `{policy_id[:64]}` — 只接受精確 rule ID。"

    policy_path = POLICIES_DIR / f"{policy_id}.json"
    if not policy_path.exists():
        return False, (f"❌ 找不到 policy `{policy_id}`\n"
                       f"（proposal 或 candidate ID 唔可以直接 pin；用 `/proposals` 睇待審批項目。）")

    try:
        d = json.loads(policy_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"❌ 讀取 policy 失敗: {e}"

    status = d.get("status", "active")
    if status != "active":
        return False, (f"❌ `{policy_id}` 狀態係 `{status}`,唔係 `active` —— 只可以 pin active policy。\n"
                       f"（archived policy 要先復原,`/pin` 唔會順便 approve 或 compile。）")

    was = bool(d.get("pinned", False))
    target = bool(pinned)
    version = d.get("version", 1)

    if was == target:
        return True, (f"ℹ️ `{policy_id}` {'已經係 pinned' if was else '本來就唔係 pinned'},未作改動。\n"
                      f"• pinned: `{was}` → `{target}`（no-op）\n"
                      f"• version: `{version}`（未 bump）\n"
                      f"• path: `{policy_path}`")

    before = {k: v for k, v in d.items() if k not in _PIN_MUTABLE}
    now = int(time.time())
    d["pinned"] = target
    d["last_updated"] = now
    if target:
        d["pinned_by"] = actor
        d["pinned_at"] = now
    else:
        d.pop("pinned_by", None)
        d.pop("pinned_at", None)

    after = {k: v for k, v in d.items() if k not in _PIN_MUTABLE}
    if before != after:
        return False, f"❌ 中止：pin 操作會改動 `{policy_id}` 嘅非 pin 欄位,已放棄寫入。"

    policy_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("📌 [Policy %s] %s by %s", "Pinned" if target else "Unpinned", policy_id, actor)

    tail = ("已免疫 staleness cleanup。" if target
            else "已恢復正常 lifecycle / staleness 管理。")
    return True, (f"{'📌 已釘住' if target else '📍 已解除釘住'} `{policy_id}`\n"
                  f"• pinned: `{was}` → `{target}`\n"
                  f"• version: `{version}`（未 bump）\n"
                  f"• status: `{status}`\n"
                  f"• path: `{policy_path}`\n"
                  f"{tail}")


def list_pending_proposals() -> List[Dict[str, Any]]:
    """List all proposals awaiting human approval."""
    results = []
    for f in sorted(PROPOSALS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("status") == "pending_approval":
                results.append(d)
        except: pass
    return results
