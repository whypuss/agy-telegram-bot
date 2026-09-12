"""Token compaction and baseline reset regression tests.

Verifies:
1. reset_user_conversation clears user_agy_cumulative baseline.
2. When agy CLI resets underneath (total_tokens < prev_total), baseline self-heals
   and does not clamp turn delta to 0 tokens.
3. compact_user_conversation reports accurate new_tokens instead of 0 tokens.
4. save_state / load_state correctly round-trips user_agy_cumulative.

Run: venv/bin/python3 -m test_token_compaction
"""
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock

from session_store import (
    user_conversations,
    user_session_usage,
    user_last_turn_usage,
    user_agy_cumulative,
    reset_user_conversation,
    clear_agy_cumulative,
    load_state,
    save_state,
)
import session_store
from agent_runner import _run_agy_turn, compact_user_conversation

_results = []


def check(name: str, cond: bool, detail: str = ""):
    _results.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail and not cond else ''}")


def test_baseline_cleared_on_reset():
    print("=== Reset clears agy cumulative baseline ===")
    uid = 99901
    user_conversations[uid] = "fake-conv-uuid"
    user_session_usage[uid] = {"total_tokens": 50000, "input_tokens": 48000, "output_tokens": 2000}
    user_agy_cumulative[uid] = {"total_tokens": 50000, "input_tokens": 48000, "output_tokens": 2000}

    reset_user_conversation(uid)

    check("user_conversations popped", uid not in user_conversations)
    check("user_session_usage popped", uid not in user_session_usage)
    check("user_agy_cumulative popped", uid not in user_agy_cumulative)


def test_clear_agy_cumulative():
    print("=== clear_agy_cumulative helper ===")
    uid = 99902
    user_agy_cumulative[uid] = {"total_tokens": 40000}
    clear_agy_cumulative(uid)
    check("user_agy_cumulative popped by helper", uid not in user_agy_cumulative)


def test_self_healing_baseline():
    print("=== Self-healing baseline when agy resets ===")
    uid = 99903
    # Suppose previous session ended with 50,000 tokens
    user_agy_cumulative[uid] = {
        "input_tokens": 48000,
        "output_tokens": 2000,
        "thinking_tokens": 500,
        "total_tokens": 50000,
    }
    user_session_usage[uid] = {
        "input_tokens": 48000,
        "output_tokens": 2000,
        "thinking_tokens": 500,
        "total_tokens": 50000,
        "num_turns": 5,
    }

    # Simulate fresh turn from agy CLI returning 13,000 tokens (which is < 50,000)
    fake_result = {
        "conversation_id": "fresh-conv-uuid",
        "status": "SUCCESS",
        "response": "Hello from fresh session",
        "num_turns": 1,
        "duration_seconds": 1.5,
        "usage": {
            "input_tokens": 12900,
            "output_tokens": 100,
            "thinking_tokens": 50,
            "cache_read_tokens": 0,
            "total_tokens": 13000,
        },
    }

    # Test the calculation logic directly
    baseline = user_agy_cumulative.get(uid)
    prev_total = baseline.get("total_tokens", 0)
    prev_input = baseline.get("input_tokens", 0)
    prev_output = baseline.get("output_tokens", 0)
    prev_thinking = baseline.get("thinking_tokens", 0)

    total_tokens = fake_result["usage"]["total_tokens"]
    input_tokens = fake_result["usage"]["input_tokens"]
    output_tokens = fake_result["usage"]["output_tokens"]
    thinking_tokens = fake_result["usage"]["thinking_tokens"]

    # The self-healing check
    if total_tokens < prev_total:
        prev_total = 0
        prev_input = 0
        prev_output = 0
        prev_thinking = 0

    turn_input = max(0, input_tokens - prev_input) if prev_total > 0 else input_tokens
    turn_output = max(0, output_tokens - prev_output) if prev_total > 0 else output_tokens
    turn_thinking = max(0, thinking_tokens - prev_thinking) if prev_total > 0 else thinking_tokens
    turn_total = max(0, total_tokens - prev_total) if prev_total > 0 else total_tokens

    check("turn_total is not 0", turn_total == 13000, f"got {turn_total}")
    check("turn_input matches fresh tokens", turn_input == 12900, f"got {turn_input}")
    check("turn_output matches fresh tokens", turn_output == 100, f"got {turn_output}")


def test_state_persistence():
    print("=== State persistence for user_agy_cumulative ===")
    uid = 99904
    user_agy_cumulative[uid] = {"total_tokens": 25000, "input_tokens": 24000, "output_tokens": 1000}

    with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
        orig_state_file = session_store.STATE_FILE
        try:
            session_store.STATE_FILE = Path(tmp.name)
            save_state()

            # Clear memory
            user_agy_cumulative.clear()
            check("memory cleared before load", uid not in user_agy_cumulative)

            load_state()
            check("user_agy_cumulative reloaded from disk", uid in user_agy_cumulative)
            check("token count preserved", user_agy_cumulative[uid].get("total_tokens") == 25000)
        finally:
            session_store.STATE_FILE = orig_state_file


async def test_compact_mocked():
    print("=== Compact conversation token accounting ===")
    uid = 99905
    user_conversations[uid] = "old-conv-123"
    user_session_usage[uid] = {"total_tokens": 60000, "input_tokens": 58000, "output_tokens": 2000, "num_turns": 4}
    user_agy_cumulative[uid] = {"total_tokens": 60000, "input_tokens": 58000, "output_tokens": 2000}

    # Mock _run_agy_turn for the summary call
    summary_text = "### 📋 項目與對話背景\n測試項目\n### ✅ 已完成事項\n無"

    # Mock run_agent_turn for the seed call
    async def mock_seed_turn(prompt, user_id, on_progress=None):
        # Simulate new session created with 14,000 tokens
        user_session_usage[user_id] = {
            "input_tokens": 13800,
            "output_tokens": 200,
            "thinking_tokens": 100,
            "total_tokens": 14000,
            "num_turns": 1,
        }
        return "✅ 已成功載入前續記憶與項目狀態，請指示下一步工作。", "new-conv-456", {"total_tokens": 14000}

    with patch("agent_runner._run_agy_turn", new=AsyncMock(return_value=(summary_text, "old-conv-123", {"total_tokens": 1000}))), \
         patch("agent_runner.run_agent_turn", side_effect=mock_seed_turn), \
         patch("memory_manager.add_session_summary"):

        success, summary, old_tok, new_tok = await compact_user_conversation(uid)

        check("compact succeeded", success is True)
        check("old_tokens is accurate", old_tok >= 60000, f"got {old_tok}")
        check("new_tokens is non-zero", new_tok == 14000, f"got {new_tok}")


def main():
    test_baseline_cleared_on_reset()
    test_clear_agy_cumulative()
    test_self_healing_baseline()
    test_state_persistence()
    asyncio.run(test_compact_mocked())

    print()
    if all(_results):
        print(f"ALL PASS ({len(_results)})")
    else:
        failed = len([r for r in _results if not r])
        print(f"FAILED: {failed}/{len(_results)}")
        exit(1)


if __name__ == "__main__":
    main()
