"""
Trajectory-based Root Cause Evaluator.
Pure functional evaluator without any tools, execution environments, or sandboxes.
"""
import json
import logging
import os
import httpx
from typing import Dict, Any, List

from evolution.schema import EvolutionCandidate

logger = logging.getLogger("agy-tg-bot.evolution.evaluator")

# SENSENOVA or OpenRouter config
SENSENOVA_API_KEY = os.getenv("SENSENOVA_API_KEY", "")
SENSENOVA_BASE_URL = os.getenv("SENSENOVA_BASE_URL", "https://token.sensenova.cn/v1")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

_EVALUATOR_PROMPT = """\
你是一個自主 Agent 系統的行為根因分析專家（Root Cause Evaluator）。
請審查輸入的對話執行軌跡（包含用戶要求、工具調用、工具輸出、Agent 回覆及用戶的反饋糾偏）。

核心目標：
不要只看用戶最後一句話！必須從工具調用與執行結果中找出造成問題的行為模式（Root Cause）。

【分析要點】：
1. Scope Escalation（擅自越權）：用戶要求「檢查/查看」，Agent 卻執行了「修改/重啟/刪除」。
2. Blind Mutation（盲目變更）：在未取得現狀 audit、未驗證備份的情況下直接改動生產環境。
3. Repeated Tool Failure（無效重試）：反覆調用同一失敗命令而未排查底層原因。
4. Explicit User Correction（用戶糾偏）：用戶表達了強烈不滿或糾正（如「別再做假設」、「先確認再改」、「不要教我改代碼」）。

【分類維度（四層分家）】：
- user_profile: 用戶長期通訊/格式/排版偏好（例如：直接給結論、禁止反問要不要）。
- system_facts: 客觀環境事實（IP、端口、硬體型號、系統版本、固定限制）。
- policy_proposal: 涉及安全邊界、生產伺服器、網絡路由的強制操作規範（高風險，需審批）。
- skill_patch: 針對通用類別任務（如 server-operations, network-device, bot-deployment）的 Pitfalls 或 SOP 修正。

【過濾原則】：
- 臨時詞過濾：若用戶只是說「今天直接給我結論」、「這次先這樣」、「剛剛那句不用了」，scope 必須設為 "temporary"。
- 若對話正常順暢、沒有新事實、沒有糾偏，candidates 必須為空數組 []。

【證據門檻 — policy_proposal 與 skill_patch 專用】：
呢兩類會變成長期規則，必須同時提供 affected_behavior 同 evidence，否則一定被 Gate 駁回。
- affected_behavior: 這條規則管束的具體行為（例如「重啟生產 gateway 前的前置檢查」）。
- evidence: 必須係軌跡入面**實際出現過**的原文片段 —— 工具輸出、錯誤訊息、測試結果、用戶糾偏原話。
- 嚴禁自行撰寫、推斷或美化 evidence。軌跡入面搵唔到實際片段，就唔好提出呢個 candidate。
- 下列**不算**證據，會被駁回：
  * 主觀結論（「已修復」「應該冇問題」「verified」「works now」）
  * 淨係 exit code 0 / 「命令成功」而冇任何行為結果
  * 泛用檢查（lint 通過、import 成功、JSON parse 成功）—— 除非受影響行為本身就係嗰項檢查
  * 同 affected_behavior 無關的輸出
- evidence 必須同 affected_behavior 有直接關係，用返同樣嘅詞彙。

嚴格輸出 JSON 格式：
{
  "candidates": [
    {
      "candidate_type": "user_profile" | "system_facts" | "policy_proposal" | "skill_patch",
      "scope": "permanent" | "temporary",
      "rule_id": "kebab-case-identifier",
      "summary": "一句話指令式核心準則（50字內，指令式語言，嚴禁廢話）",
      "root_cause": "導致該提議的根本原因分析（50字內）",
      "affected_behavior": "這條規則管束的具體行為（policy_proposal / skill_patch 必填）",
      "evidence": "軌跡中實際出現過的原文片段：工具輸出、錯誤訊息、測試結果或用戶糾偏原話。不得自行撰寫。",
      "confidence": 0.95,
      "pinned": true | false,
      "skill_class": "server-operations" | "network-device" | "bot-deployment" | null,
      "requires_approval": true | false
    }
  ]
}
"""

async def evaluate_trajectory(trajectory: Dict[str, Any]) -> List[EvolutionCandidate]:
    """Pure functional trajectory evaluation without any tool capabilities."""
    raw_snippet = json.dumps(trajectory, ensure_ascii=False, indent=2)
    if len(raw_snippet) > 8000:
        # Keep head and tail to stay within comfortable LLM context window
        raw_snippet = raw_snippet[:4000] + "\n... [truncated] ...\n" + raw_snippet[-3500:]

    client_headers = {}
    api_url = ""
    model_name = ""

    if SENSENOVA_API_KEY:
        api_url = f"{SENSENOVA_BASE_URL.rstrip('/')}/chat/completions"
        client_headers = {"Authorization": f"Bearer {SENSENOVA_API_KEY}"}
        model_name = "sensenova-6.8-flash-lite"
    elif OPENROUTER_API_KEY:
        api_url = "https://openrouter.ai/api/v1/chat/completions"
        client_headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
        model_name = "google/gemini-2.0-flash-001"
    else:
        logger.warning("No auxiliary API key configured for evaluator.")
        return []

    try:
        async with httpx.AsyncClient(timeout=35) as client:
            resp = await client.post(
                api_url,
                headers=client_headers,
                json={
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": _EVALUATOR_PROMPT},
                        {"role": "user", "content": f"【完整執行軌跡 Trajectory】:\n{raw_snippet}"}
                    ],
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"}
                }
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            res_json = json.loads(content)
            raw_cands = res_json.get("candidates", [])

            candidates = []
            for c in raw_cands:
                cand_type = c.get("candidate_type", "user_profile")
                req_app = True if cand_type in ("policy_proposal", "skill_patch") else bool(c.get("requires_approval", False))
                candidates.append(EvolutionCandidate(
                    candidate_type=cand_type,
                    scope=c.get("scope", "permanent"),
                    rule_id=c.get("rule_id", f"rule-{int(c.get('confidence', 0)*100)}"),
                    summary=c.get("summary", "").strip(),
                    root_cause=c.get("root_cause", "").strip(),
                    confidence=float(c.get("confidence", 0.8)),
                    evidence=c.get("evidence", ""),
                    affected_behavior=(c.get("affected_behavior") or "").strip(),
                    pinned=bool(c.get("pinned", False)),
                    skill_class=c.get("skill_class"),
                    requires_approval=req_app
                ))
            return candidates

    except Exception as e:
        logger.warning("Trajectory evaluation call failed: %s", e)
        return []
