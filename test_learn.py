"""
Unit & Integration Tests for Antigravity /learn Protocol and Continuous Learner.
Run: venv/bin/python3 -m test_learn
"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import learner
from policy_store import get_active_policies, set_policy_status
from memory_manager import read_entries, build_memory_context

_results = []


def check(name: str, cond: bool, detail: str = ""):
    _results.append(bool(cond))
    status = "PASS" if cond else "FAIL"
    msg = f"  {status}  {name}"
    if detail and not cond:
        msg += f" — {detail}"
    print(msg)


def test_correction_signals():
    print("=== Correction Signal Detection ===")
    positives = [
        "不要再隨便修改 server 配置",
        "這不對，應該先檢查端口",
        "你做錯了，先確認現況",
        "誰叫你重啟容器的？",
        "嚴禁在未備份前覆蓋資料庫",
        "記住以後只用繁體中文",
        "你忘了之前說過不要這樣嗎？",
        "別再重試了",
    ]
    negatives = [
        "今天天氣怎麼樣？",
        "幫我寫一個快速排序算法",
        "查詢一下目前的 git 分支",
        "謝謝你，做得很好！",
    ]

    for p in positives:
        check(f"detects correction: '{p}'", learner.detect_correction_signals(p))

    for n in negatives:
        check(f"ignores non-correction: '{n}'", not learner.detect_correction_signals(n))


def test_json_extraction():
    print("=== Learn JSON Extraction ===")
    sample_valid = """
經過審視，本會話存在操作問題。
```json
{
  "learned": true,
  "summary": "在修改服務器前先備份",
  "policies": [
    {
      "id": "rule_test_backup_first",
      "summary": "修改設定檔前必須先建立備份副本",
      "root_cause": "誤改配置導致服務短暫中斷"
    }
  ],
  "user_preferences": [
    "偏好以繁體中文回覆"
  ],
  "system_facts": [
    "生產伺服器端口為 8790"
  ]
}
```
已完成學習。
"""
    extracted = learner.extract_learn_json(sample_valid)
    check("extracts valid json from markdown block", extracted is not None)
    check("summary extracted correctly", extracted.get("summary") == "在修改服務器前先備份")
    check("policies list present", len(extracted.get("policies", [])) == 1)

    no_learn_sample = """
本次為一般問答，無新經驗。
```json
{
  "learned": false
}
```
"""
    extracted_none = learner.extract_learn_json(no_learn_sample)
    check("extracts learned=false correctly", extracted_none is not None and not extracted_none.get("learned"))

    corrupted_sample = "文字中沒有 json 塊"
    check("returns None for non-json text", learner.extract_learn_json(corrupted_sample) is None)


def test_apply_and_injection():
    print("=== Artifact Persistence & Prompt Injection ===")
    temp_dir = Path(tempfile.mkdtemp(prefix="test_learn_mem_"))
    try:
        with patch("config.MEMORY_DIR", temp_dir), \
             patch("policy_store.POLICIES_DIR", temp_dir / "policies"), \
             patch("memory_manager.MEMORY_DIR", temp_dir):

            (temp_dir / "policies").mkdir(parents=True, exist_ok=True)

            payload = {
                "learned": True,
                "summary": "部署前必須先測試",
                "policies": [
                    {
                        "id": "rule_verify_before_deploy",
                        "summary": "任何生產部署必須先通過本地健康檢查",
                        "root_cause": "直接推送造成 502 報錯",
                        "trigger": "執行 deploy 命令時",
                        "constraint": "嚴禁略過 curl 檢查",
                        "verification": "curl -I 回傳 200"
                    }
                ],
                "user_preferences": [
                    "永遠不要假設部署成功，必須檢視日誌"
                ],
                "system_facts": [
                    "測試機端口為 8895"
                ]
            }

            stats = learner.apply_learned_artifacts(payload, source_label="test")
            check("stats learned flag is True", stats["learned"])
            check("policy reported saved", len(stats["policies_saved"]) == 1)
            check("preference reported saved", len(stats["prefs_saved"]) == 1)
            check("fact reported saved", len(stats["facts_saved"]) == 1)

            # Verify policy is in force
            active = get_active_policies()
            check("active policies count is 1", len(active) == 1)
            check("policy id matches", active[0]["id"] == "rule_verify_before_deploy")
            check("policy summary matches", "健康檢查" in active[0]["summary"])

            # Verify prompt injection
            prompt_ctx = build_memory_context()
            check("policy injected into prompt context", "rule_verify_before_deploy" in prompt_ctx or "健康檢查" in prompt_ctx)
            check("hardline heading is present", "HARDLINE BEHAVIOR POLICIES" in prompt_ctx)
            check("user preference is injected", "永遠不要假設部署成功" in prompt_ctx)
            check("system fact is injected", "8895" in prompt_ctx)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_card_formatting():
    print("=== Card Formatting ===")
    explanation = "回顧本次操作，總結了 1 項硬性規則與 1 項偏好。\n```json\n{\"learned\": true}\n```"
    stats = {
        "learned": True,
        "summary": "核心規則提煉",
        "policies_saved": [{"id": "rule_test", "summary": "必須先確認"}],
        "prefs_saved": ["偏好繁體中文"],
        "facts_saved": ["IP: 1.2.3.4"],
    }
    card = learner.format_learn_card(explanation, stats)
    check("contains title", "Antigravity /learn" in card)
    check("strips raw json block", "```json" not in card)
    check("lists policy id", "rule_test" in card)
    check("lists user preference", "偏好繁體中文" in card)
    check("lists system fact", "1.2.3.4" in card)


def test_background_auto_learner():
    print("=== Background Auto-Learner & Reviewer ===")
    import asyncio
    temp_dir = tempfile.mkdtemp()
    try:
        mock_p_dir = Path(temp_dir) / "policies"
        mock_p_dir.mkdir()
        mock_user = Path(temp_dir) / "USER.md"
        mock_mem = Path(temp_dir) / "MEMORY.md"

        with patch("policy_store.POLICIES_DIR", mock_p_dir), \
             patch("memory_manager.MEMORY_DIR", Path(temp_dir)):

            notified_messages = []
            async def mock_notify(msg: str):
                notified_messages.append(msg)

            # 1. Test auto_learn_from_turn immediate heuristic extraction
            test_prompt = "你做錯了，不要再隨便修改線上資料庫！"
            test_resp = "好的，我已經停止修改。"
            learner.auto_learn_from_turn(999, test_prompt, test_resp)
            user_entries = read_entries("user")
            check("heuristic extracts constraint to USER.md", any("不要再隨便修改線上資料庫" in e for e in user_entries))

            # 2. Test run_background_reviewer synthesis with mocked model response
            mock_reviewer_reply = """\
深度根因分析完成。
```json
{
  "learned": true,
  "summary": "線上資料庫防誤改保護",
  "policies": [
    {
      "id": "rule_db_protect",
      "summary": "嚴禁在無唯讀保護或未備份情況下直接執行線上資料庫變更",
      "root_cause": "線上資料庫具有不可逆性",
      "trigger": "涉及生產環境資料庫操作",
      "constraint": "先備份並確認唯讀權限",
      "verification": "檢查備份檔案存在性"
    }
  ],
  "user_preferences": [],
  "system_facts": []
}
```
"""
            async def run_async_test():
                with patch("agent_runner._run_agy_turn", return_value=(mock_reviewer_reply, None, None)):
                    res = await learner.run_background_reviewer(999, test_prompt, test_resp, notify_callback=mock_notify)
                    return res

            res = asyncio.run(run_async_test())
            check("reviewer reported learned=true", res.get("learned") is True)
            check("policy saved from reviewer", len(res.get("policies_saved", [])) == 1)
            active_p = get_active_policies()
            check("active policies has rule_db_protect", any(p["id"] == "rule_db_protect" for p in active_p))
            check("notification callback was triggered", len(notified_messages) == 1)
            check("notification contains rule id", "rule_db_protect" in notified_messages[0])

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    test_correction_signals()
    test_json_extraction()
    test_apply_and_injection()
    test_card_formatting()
    test_background_auto_learner()

    print("\n" + (f"ALL PASS ({len(_results)})" if all(_results)
                  else f"FAILURES: {_results.count(False)}/{len(_results)}"))
    return 0 if all(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
