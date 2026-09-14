"""Tests for unfolded thinking process display in status cards and replies."""
import json
import os
import re
import tempfile
from formatter import format_markdown_v2, split_markdown_chunks, utf16_len
from agent_runner import _parse_step_update_to_indicator, _extract_last_thinking

_results = []

def check(name, cond, detail=""):
    _results.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail and not cond else ''}")

def test_extract_last_thinking():
    print("=== Extract thinking from transcript ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        # Mock brain logs directory structure
        conv_id = "test-conv-uuid-1234"
        log_dir = os.path.expanduser(f"~/.gemini/antigravity-cli/brain/{conv_id}/.system_generated/logs")
        os.makedirs(log_dir, exist_ok=True)
        t_file = os.path.join(log_dir, "transcript_full.jsonl")
        
        try:
            # Multi-turn transcript
            lines = [
                # Turn 1
                {"type": "USER_INPUT", "step_index": 0, "content": "turn 1 prompt"},
                {"type": "PLANNER_RESPONSE", "step_index": 1, "thinking": "第一回合的思考分析內容"},
                # Turn 2
                {"type": "USER_INPUT", "step_index": 2, "content": "turn 2 prompt"},
                {"type": "PLANNER_RESPONSE", "step_index": 3, "thinking": "第二回合第一步思考分析內容"},
                {"type": "PLANNER_RESPONSE", "step_index": 4, "thinking": "第二回合第二步思考分析內容"},
            ]
            with open(t_file, "w", encoding="utf-8") as f:
                for item in lines:
                    f.write(json.dumps(item) + "\n")
            
            extracted = _extract_last_thinking(conv_id)
            check("extracts latest turn only", "第一回合" not in (extracted or ""))
            check("joins multiple thinking steps in turn", "第二回合第一步思考分析內容\n\n第二回合第二步思考分析內容" == (extracted or ""))
        finally:
            if os.path.exists(t_file):
                os.remove(t_file)
            if os.path.exists(log_dir):
                try:
                    os.removedirs(log_dir)
                except Exception:
                    pass

def test_indicator_parsing():
    print("=== Indicator parsing with thinking ===")
    # When agent_response is DONE and thinking_tokens > 0
    su = {
        "step_type": "agent_response",
        "state": "DONE",
        "duration_seconds": 1.2,
        "usage": {"thinking_tokens": 150},
        "conversation_id": "nonexistent-id",
    }
    ind = _parse_step_update_to_indicator(su)
    check("indicates thinking step", ind is not None and "🧠" in ind)

def test_status_card_thinking_injection():
    print("=== Status card thinking injection contract ===")
    th_sample = "Analysis of current code structure: Identified that trajectory effort locks upon creation."
    clean_th = re.sub(r'```[\s\S]*?```', '', th_sample).strip()
    if len(clean_th) > 350:
        clean_th = clean_th[:340] + "..."

    # Case 1: Placeholder existed in progress_items
    progress_items = [
        "✓ 🧠 深度思考完成 (1.5s)",
        "✓ 🔧 瀏覽目錄清單",
        "✓ 📝 回答生成完畢 (0.8s)"
    ]
    final_items = list(progress_items)
    thinking_injected = False
    for idx, it in enumerate(final_items):
        if "🧠" in it or "思考" in it or "步驟規劃" in it:
            if not thinking_injected:
                dur_match = re.search(r'\(\d+\.?\d*s\)', it)
                dur_suffix = f" {dur_match.group(0)}" if dur_match else ""
                final_items[idx] = f"✓ 🧠 思考過程{dur_suffix}：\n{clean_th}"
                thinking_injected = True
            else:
                final_items[idx] = ""
    final_items = [item for item in final_items if item]

    check("placeholder replaced with actual thinking", any("思考過程 (1.5s)：\nAnalysis of current code" in it for it in final_items))
    check("no raw 深度思考完成 placeholder remaining", not any("深度思考完成" in it for it in final_items))

    # Case 2: No placeholder existed (1-step answer)
    progress_items_short = [
        "✓ 📝 回答生成完畢 (0.8s)"
    ]
    final_items_short = list(progress_items_short)
    thinking_injected = False
    for idx, it in enumerate(final_items_short):
        if "🧠" in it or "思考" in it or "步驟規劃" in it:
            final_items_short[idx] = f"✓ 🧠 思考過程：\n{clean_th}"
            thinking_injected = True
            break
    if not thinking_injected:
        final_items_short.insert(0, f"✓ 🧠 思考過程：\n{clean_th}")

    check("inserted thinking at beginning when no placeholder", final_items_short[0].startswith("✓ 🧠 思考過程："))

    # Case 3: Message length and MarkdownV2 safety
    completed_block = "\n".join(final_items)
    status_text = (
        f"✅ *任務完成* \\(耗時 2s · 1,500 tokens\\)\n"
        f"🔌 後端: ⚡ Antigravity\n"
        f"📌 模型: `Gemini 3.8 Flash (High)`\n\n"
        f"📋 *執行過程：*\n"
        f"{format_markdown_v2(completed_block)}"
    )
    chunks = split_markdown_chunks(status_text)
    check("status card fits within single chunk", len(chunks) == 1 and utf16_len(chunks[0]) <= 4096)

def test_extract_last_response():
    print("=== Extract last response from transcript ===")
    from agent_runner import _extract_last_response_from_transcript
    with tempfile.TemporaryDirectory() as tmpdir:
        conv_id = "test-conv-uuid-response-999"
        log_dir = os.path.expanduser(f"~/.gemini/antigravity-cli/brain/{conv_id}/.system_generated/logs")
        os.makedirs(log_dir, exist_ok=True)
        t_file = os.path.join(log_dir, "transcript_full.jsonl")
        try:
            lines = [
                {"type": "USER_INPUT", "step_index": 0, "content": "hello turn 1"},
                {"type": "PLANNER_RESPONSE", "step_index": 1, "content": "turn 1 answer"},
                {"type": "USER_INPUT", "step_index": 2, "content": "hello turn 2"},
                {"type": "PLANNER_RESPONSE", "step_index": 3, "tool_calls": [{"name": "run_command"}]},
                {"type": "GENERIC", "step_index": 4, "content": "command output"},
                {"type": "PLANNER_RESPONSE", "step_index": 5, "content": "這是最終真實答覆內容"},
            ]
            with open(t_file, "w", encoding="utf-8") as f:
                for item in lines:
                    f.write(json.dumps(item) + "\n")

            resp = _extract_last_response_from_transcript(conv_id)
            check("extracts latest turn final response", resp == "這是最終真實答覆內容")
        finally:
            if os.path.exists(t_file):
                os.remove(t_file)
            if os.path.exists(log_dir):
                try:
                    os.removedirs(log_dir)
                except Exception:
                    pass

def main():
    test_extract_last_thinking()
    test_indicator_parsing()
    test_status_card_thinking_injection()
    test_extract_last_response()
    passed = sum(_results)
    print(f"\n{passed}/{len(_results)} checks passed")
    return 0 if passed == len(_results) else 1

if __name__ == "__main__":
    import sys
    sys.exit(main())
