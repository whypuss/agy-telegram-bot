"""
Antigravity Native Learning & Continuous Rule Synthesis Engine (learner.py)
===========================================================================
Implements the Google Antigravity /learn protocol for both explicit Telegram commands
and continuous background task learning:
- Deeply inspects conversation history, user corrections, constraints, overrides, and failures.
- Classifies learned outcomes into:
    1. Hardline Behavioral Policies (persisted to policies/*.json)
    2. User Preferences & Working Style (persisted to USER.md)
    3. System & Environmental Facts (persisted to MEMORY.md)
- Supports non-blocking continuous auto-learning when user corrections are detected.
"""

import json
import logging
import re
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

from config import DEFAULT_MODEL, is_opencode_model
from memory_manager import add_entry
from policy_store import save_policy
from session_store import get_user_conversation, get_user_model, get_user_oc_session

logger = logging.getLogger("agy-tg-bot.learner")

# ---------------------------------------------------------------------------
# Antigravity Native /learn System Instruction Template
# Derived from Google Antigravity agy binary <LEARN> specification
# ---------------------------------------------------------------------------
ANTIGRAVITY_LEARN_INSTRUCTION = """\
【系統指令：Google Antigravity 原生 /learn 行為學習與經驗提煉協議】
你正在執行 Antigravity 原生的 /learn 學習機制。
請深入審視本會話的所有交互歷程、近期操作與輸出結果，優先檢視使用者的明確指正、約束、不滿、失敗重試與成功突破。

## 核心分析流程 (Identify What to Learn)
1. **分析用戶訊息**：重點分析近期用戶訊息中的明確糾正、限制、覆寫或指針（例如「不對」、「不要」、「應該」、「先確認」、「不要再」等）。
2. **鎖定關鍵修復**：對比失敗嘗試與成功解決方案，找出關鍵分水嶺與轉折點。
3. **定位根本原因與範疇 (Root Cause & Scope)**：解決深層根因與邊界條件，而非表面症狀。
4. **驗證是否需要學習**：若本次交互未揭示任何新的可複用行為、限制或事實（例如僅是普通查詢或閒聊），請坦誠說明無需變更並退出。

## 經驗分類 (Classification)
1. **硬性行為守則 (Hardline Rule / Policy)**：具有通用約束力的操作邊界、驗證原則、命令禁忌或安全防線。
2. **用戶偏好與風格 (User Preference)**：使用者的溝通習慣、命名規範、個人喜好或特定要求（存入 USER.md）。
3. **系統與環境事實 (System Fact)**：新發現的伺服器端點、IP、埠號、密碼規範或部署環境特性（存入 MEMORY.md）。

## 輸出結構規範
請先以繁體中文給出清晰、條理分明的「學習反思與提煉總結」。
在回答的最末尾，務必附帶一個嚴格合法的 JSON 區塊（系統將自動提取並持久化至磁碟）：
```json
{
  "learned": true,
  "summary": "本次學習的核心要點摘要（一句話）",
  "policies": [
    {
      "id": "rule_簡短英數識別碼",
      "summary": "一句話描述必須嚴格遵守的操作約束或禁止事項",
      "root_cause": "為什麼需要此規則的根因說明",
      "trigger": "觸發此規則的情境（可選）",
      "constraint": "具體執行的硬性約束（可選）",
      "verification": "如何驗證是否遵守的檢查方式（可選）"
    }
  ],
  "user_preferences": [
    "提煉的使用者習慣或偏好條目"
  ],
  "system_facts": [
    "提煉的系統/環境事實條目"
  ]
}
```
注意：如果經過審視，本次對話完全沒有需要持久化的新規則或偏好，請將 "learned" 設為 false，policies / user_preferences / system_facts 置為空陣列。
"""


# ---------------------------------------------------------------------------
# Correction Signal Detector
# ---------------------------------------------------------------------------
CORRECTION_PATTERNS = [
    r'不要再?',
    r'別再?',
    r'不對',
    r'不是這樣',
    r'錯了',
    r'你做錯',
    r'你又',
    r'應該',
    r'必須',
    r'嚴禁',
    r'先確認',
    r'先檢查',
    r'不要假設',
    r'你忘了',
    r'誰叫你',
    r'記住',
    r'教訓',
    r'反省',
    r'檢討',
]

_CORRECTION_REGEX = re.compile("|".join(CORRECTION_PATTERNS), re.IGNORECASE)


def detect_correction_signals(text: str) -> bool:
    """Detect if a user prompt contains strong correction, reproach, or invariant constraints."""
    if not text or len(text.strip()) < 2:
        return False
    return bool(_CORRECTION_REGEX.search(text))


# ---------------------------------------------------------------------------
# Artifact Extraction & Persistence
# ---------------------------------------------------------------------------
def extract_learn_json(response_text: str) -> Optional[dict]:
    """Extract and parse the structured JSON block from the agent's learning response."""
    if not response_text:
        return None

    # Try code block enclosed json first
    matches = re.findall(r'```(?:json)?\s*([\s\S]*?)\s*```', response_text)
    for m in reversed(matches):
        m_strip = m.strip()
        if '"learned"' in m_strip or '"policies"' in m_strip:
            try:
                data = json.loads(m_strip)
                if isinstance(data, dict):
                    return data
            except Exception:
                continue

    # Fallback: scan for any raw JSON object containing "learned"
    json_candidates = re.findall(r'\{[^{}]*"learned"[\s\S]*?\}', response_text)
    for jc in json_candidates:
        try:
            data = json.loads(jc)
            if isinstance(data, dict):
                return data
        except Exception:
            continue

    return None


def apply_learned_artifacts(learned_data: dict, source_label: str = "learn") -> dict:
    """
    Apply extracted learning artifacts directly into the corresponding storage backends:
    - policies -> policy_store.save_policy
    - user_preferences -> memory_manager.add_entry('user')
    - system_facts -> memory_manager.add_entry('memory')
    """
    stats = {
        "learned": bool(learned_data.get("learned", False)),
        "summary": str(learned_data.get("summary", "")).strip(),
        "policies_saved": [],
        "prefs_saved": [],
        "facts_saved": [],
        "errors": [],
    }

    if not stats["learned"]:
        return stats

    # 1. Save Policies
    raw_policies = learned_data.get("policies") or []
    if isinstance(raw_policies, list):
        for pol in raw_policies:
            if not isinstance(pol, dict):
                continue
            p_id = pol.get("id") or f"rule_learned_{int(time.time())}"
            summary = pol.get("summary") or ""
            root_cause = pol.get("root_cause") or ""
            trigger = pol.get("trigger") or ""
            constraint = pol.get("constraint") or ""
            verification = pol.get("verification") or ""

            if summary.strip():
                ok, msg = save_policy(
                    policy_id=p_id,
                    summary=summary,
                    root_cause=root_cause,
                    trigger=trigger,
                    constraint=constraint,
                    verification=verification,
                    evidence=f"Extracted via Antigravity /learn ({time.strftime('%Y-%m-%d')})",
                )
                if ok:
                    stats["policies_saved"].append({"id": p_id, "summary": summary})
                else:
                    stats["errors"].append(msg)

    # 2. Save User Preferences
    raw_prefs = learned_data.get("user_preferences") or []
    if isinstance(raw_prefs, list):
        for pref in raw_prefs:
            pref_str = str(pref).strip()
            if len(pref_str) >= 4:
                ok, msg = add_entry("user", pref_str, source=source_label)
                if ok:
                    stats["prefs_saved"].append(pref_str)
                else:
                    stats["errors"].append(msg)

    # 3. Save System Facts
    raw_facts = learned_data.get("system_facts") or []
    if isinstance(raw_facts, list):
        for fact in raw_facts:
            fact_str = str(fact).strip()
            if len(fact_str) >= 4:
                ok, msg = add_entry("memory", fact_str, source=source_label)
                if ok:
                    stats["facts_saved"].append(fact_str)
                else:
                    stats["errors"].append(msg)

    return stats


def format_learn_card(explanation_text: str, stats: dict) -> str:
    """Format the learning result into an unfolded, readable Telegram markdown card."""
    lines = ["🎓 **Antigravity /learn 學習與規則提煉報告**\n"]

    # Strip raw json block from user display to keep card clean
    clean_explanation = re.sub(r'```(?:json)?\s*\{[\s\S]*?"learned"[\s\S]*?\}\s*```', '', explanation_text).strip()
    if clean_explanation:
        lines.append(clean_explanation)
        lines.append("\n---\n")

    if not stats.get("learned"):
        lines.append("ℹ️ **審視結論**：本次交互未發現需持久化的新操作規則或行為約束。")
        return "\n".join(lines)

    lines.append("💾 **已持久化落盤成果：**")

    pols = stats.get("policies_saved", [])
    if pols:
        lines.append(f"\n🚨 **新增硬性行為守則 ({len(pols)} 項)：**")
        for p in pols:
            lines.append(f"• `{p['id']}`: {p['summary']}")

    prefs = stats.get("prefs_saved", [])
    if prefs:
        lines.append(f"\n👤 **已更新個人偏好 ({len(prefs)} 項，寫入 USER.md)：**")
        for pr in prefs:
            lines.append(f"• {pr}")

    facts = stats.get("facts_saved", [])
    if facts:
        lines.append(f"\n🧠 **已記錄系統環境事實 ({len(facts)} 項，寫入 MEMORY.md)：**")
        for fa in facts:
            lines.append(f"• {fa}")

    lines.append("\n💡 *以上規則與記憶已即刻生效，並將在後續所有新會話中作為前置約束自動加載！*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Background Auto-Reviewer System Instruction Template
# ---------------------------------------------------------------------------
BACKGROUND_REVIEWER_INSTRUCTION = """\
【系統指令：Google Antigravity /learn 背景持續學習 Reviewer】
你正在執行背景持續經驗提煉。使用者剛才在任務中對 Agent 進行了指正、糾正或提出了硬性操作約束。

請根據以下「使用者反饋/指正」與「Agent 前前回合的行為/輸出」，進行客觀、批判性的反思：

## 使用者反饋/指正：
{prompt}

## Agent 前次處理/回應摘要：
{response_snippet}

## 分析與提煉目標：
1. 找出深層根因（Root Cause）：為什麼會被糾正？Agent 忽略了什麼前提條件或邊界限制？
2. 將經驗編譯為「硬性行為守則 (Hardline Policy)」：
   - 規則必須是可執行的硬約束（非空洞口號）。
   - 必須具備明確的觸發情境 (trigger)、硬性約束 (constraint) 與驗證檢查 (verification)。
3. 若包含使用者個人溝通或操作偏好，提煉為 user_preferences；若包含伺服器/端點/埠號等事實，提煉為 system_facts。

請在回答的最末尾，務必附帶嚴格合法的 JSON 區塊（系統將自動提取並持久化至磁碟）：
```json
{{
  "learned": true,
  "summary": "一句話總結本次提煉的核心教訓",
  "policies": [
    {{
      "id": "rule_簡短英數識別碼",
      "summary": "具體且必須嚴格遵守的操作約束或禁止事項",
      "root_cause": "為什麼需要此規則的深層根因",
      "trigger": "觸發此規則的情境",
      "constraint": "具體執行的硬性約束",
      "verification": "如何驗證是否遵守的檢查方式"
    }}
  ],
  "user_preferences": [
    "提煉的使用者習慣或偏好條目"
  ],
  "system_facts": [
    "提煉的系統/環境事實條目"
  ]
}}
```
如果經審視該指正無需提煉為通用規則，請設 learned: false，其餘陣列置空。
"""

# Cooldown tracking for background reviewer (user_id -> timestamp)
_last_auto_learn_ts: Dict[int, float] = {}
AUTO_LEARN_COOLDOWN_SECONDS = 30.0


# ---------------------------------------------------------------------------
# Learn Turn Execution (Invoked by /learn command)
# ---------------------------------------------------------------------------
async def execute_learn_turn(
    user_id: int,
    user_note: str = "",
    on_progress: Optional[Callable[[str], Coroutine]] = None,
) -> Tuple[bool, str, dict]:
    """
    Execute a dedicated Antigravity /learn turn.
    Returns:
        (success, formatted_card_text, stats_dict)
    """
    from agent_runner import run_agent_turn

    # Check active session status
    agy_conv = get_user_conversation(user_id)
    oc_sess = get_user_oc_session(user_id)
    has_active_session = bool(agy_conv or oc_sess)

    prompt_parts = [ANTIGRAVITY_LEARN_INSTRUCTION]
    if user_note.strip():
        prompt_parts.append(f"\n【使用者補充的學習重點/指令反饋】：\n{user_note.strip()}\n")
    elif not has_active_session:
        return False, "⚠️ 目前尚未建立活躍對話會話，且未提供具體學習內容。\n💡 提示：可在執行任務後輸入 `/learn`，或直接提供內容，例如 `/learn 部署前必須先測試連線`。", {}

    full_prompt = "\n".join(prompt_parts)

    if on_progress:
        await on_progress("🧠 正在回顧對話交互並執行 Antigravity /learn 深度分析...")

    try:
        reply_text, _, _ = await run_agent_turn(
            prompt=full_prompt,
            user_id=user_id,
            on_progress=on_progress,
        )
    except Exception as e:
        logger.exception("execute_learn_turn failed: %s", e)
        return False, f"❌ 執行 /learn 學習失敗: {e}", {}

    if not reply_text or reply_text.startswith("❌"):
        return False, f"❌ /learn 未能成功產出學習反思：\n{reply_text}", {}

    learned_json = extract_learn_json(reply_text)
    if not learned_json:
        # If model did not emit json, fallback to checking if text contains non-learning notice
        stats = {"learned": False, "summary": "", "policies_saved": [], "prefs_saved": [], "facts_saved": []}
    else:
        stats = apply_learned_artifacts(learned_json, source_label="learn")

    card_text = format_learn_card(reply_text, stats)
    return True, card_text, stats


# ---------------------------------------------------------------------------
# Continuous Background Auto-Learner with LLM Reviewer
# ---------------------------------------------------------------------------
async def run_background_reviewer(
    user_id: int,
    prompt: str,
    response_text: str,
    notify_callback: Optional[Callable[[str], Coroutine]] = None,
) -> dict:
    """
    Run an asynchronous background reviewer turn to synthesize hardline policies
    from user corrections without blocking the user's conversation flow.
    """
    from agent_runner import _run_agy_turn
    from config import is_opencode_model
    from session_store import get_user_model

    resp_snippet = response_text[:1200] if response_text else "（無回應）"
    reviewer_prompt = BACKGROUND_REVIEWER_INSTRUCTION.format(
        prompt=prompt[:800],
        response_snippet=resp_snippet,
    )

    try:
        model = get_user_model(user_id)
        if is_opencode_model(model):
            from opencode_runner import run_opencode_turn
            rev_reply, _, _ = await run_opencode_turn(
                prompt=reviewer_prompt,
                user_id=user_id,
                model=model,
            )
        else:
            rev_reply, _, _ = await _run_agy_turn(
                prompt=reviewer_prompt,
                user_id=user_id,
            )

        if not rev_reply or rev_reply.startswith("❌"):
            logger.warning("Background Reviewer returned empty or error reply")
            return {"learned": False}

        learned_json = extract_learn_json(rev_reply)
        if not learned_json:
            return {"learned": False}

        stats = apply_learned_artifacts(learned_json, source_label="auto-learn")

        # Proactively notify user via callback if new hardline policies were synthesized
        pols = stats.get("policies_saved", [])
        if pols and notify_callback:
            pol_lines = "\n".join(f"• `{p['id']}`: {p['summary']}" for p in pols)
            notice = (
                f"🎓 **[Antigravity 持續學習已生效]**\n"
                f"系統已自動從剛才的指正中提煉硬性行為守則並即刻落盤：\n"
                f"{pol_lines}\n\n"
                f"💡 *此守則已寫入 policies/，所有新會話均將嚴格遵守。*"
            )
            try:
                await notify_callback(notice)
            except Exception as ne:
                logger.warning("Failed to send auto-learn notification: %s", ne)

        return stats

    except Exception as e:
        logger.exception("run_background_reviewer failed: %s", e)
        return {"learned": False, "error": str(e)}


def auto_learn_from_turn(
    user_id: int,
    prompt: str,
    response_text: str,
    is_corrected_turn: bool = False,
    notify_callback: Optional[Callable[[str], Coroutine]] = None,
) -> None:
    """
    Background worker invoked after turn completion.
    Automatically captures corrections immediately and schedules the LLM reviewer.
    """
    if not prompt or not response_text:
        return

    should_learn = is_corrected_turn or detect_correction_signals(prompt)
    if not should_learn:
        return

    logger.info("Continuous Auto-Learner: correction/invariant signal detected for user %s", user_id)

    # 1. Fast heuristics: extract direct constraints immediately to USER.md
    clean_p = prompt.strip()
    for sentence in re.split(r'[。！？!?\n]+', clean_p):
        sentence = sentence.strip()
        if len(sentence) >= 6 and detect_correction_signals(sentence):
            cleaned_s = re.sub(r'^(為什麼|你怎麼|怎麼又|到底)\s*', '', sentence)
            if len(cleaned_s) >= 6:
                logger.info("Auto-Learner: auto-recording user correction to USER.md: %s", cleaned_s[:60])
                add_entry("user", cleaned_s[:300], source="auto-learn")
                break

    # 2. Cooldown check for deep LLM reviewer
    now = time.time()
    last_ts = _last_auto_learn_ts.get(user_id, 0.0)
    if now - last_ts < AUTO_LEARN_COOLDOWN_SECONDS:
        logger.info("Auto-Learner: deep reviewer skipped due to cooldown (%.1fs remaining)",
                    AUTO_LEARN_COOLDOWN_SECONDS - (now - last_ts))
        return
    _last_auto_learn_ts[user_id] = now

    # 3. Schedule asynchronous deep reviewer in background
    try:
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            asyncio.create_task(
                run_background_reviewer(
                    user_id=user_id,
                    prompt=prompt,
                    response_text=response_text,
                    notify_callback=notify_callback,
                )
            )
        else:
            logger.debug("Auto-Learner: no running event loop, skipping deep reviewer")
    except Exception as e:
        logger.warning("Auto-Learner: failed scheduling background reviewer: %s", e)

