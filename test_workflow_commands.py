"""
Unit & Integration Tests for Antigravity Workflow & Reasoning Commands.
Run: venv/bin/python3 -m test_workflow_commands
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import workflow_commands as wf
from workflow_commands import _discover_skills, _parse_duration

_results = []


def check(name: str, cond: bool, detail: str = ""):
    _results.append(bool(cond))
    status = "PASS" if cond else "FAIL"
    msg = f"  {status}  {name}"
    if detail and not cond:
        msg += f" — {detail}"
    print(msg)


def test_duration_parsing():
    print("=== Schedule Duration Parsing ===")
    check("parses raw seconds '60'", _parse_duration("60") == 60)
    check("parses suffix seconds '45s'", _parse_duration("45s") == 45)
    check("parses minutes '5m'", _parse_duration("5m") == 300)
    check("parses minutes '10min'", _parse_duration("10min") == 600)
    check("parses minutes '3分鐘'", _parse_duration("3分鐘") == 180)
    check("parses hours '2h'", _parse_duration("2h") == 7200)
    check("parses hours '1小時'", _parse_duration("1小時") == 3600)
    check("returns None for invalid 'abc'", _parse_duration("abc") is None)
    check("returns None for empty ''", _parse_duration("") is None)


def test_skills_discovery():
    print("=== Skills Discovery ===")
    skills = _discover_skills()
    check("discovers builtin skills", len(skills) > 0)
    names = [s["name"] for s in skills]
    check("contains agy-customizations or antigravity-guide",
          any("agy" in n or "anti" in n for n in names))


def test_command_prompts():
    print("=== Workflow Command Prompts & Guides ===")
    # 1. Test empty prompts give usage guide
    mock_update = MagicMock()
    mock_msg = MagicMock()
    mock_msg.reply_text = AsyncMock()
    mock_update.message = mock_msg
    mock_context = MagicMock()

    # /plan without args
    mock_msg.text = "/plan"
    asyncio.run(wf.cmd_plan(mock_update, mock_context))
    check("/plan guide sent when no args", "架構規劃模式" in mock_msg.reply_text.call_args[0][0])

    # /grill-me without args
    mock_msg.text = "/grill-me"
    asyncio.run(wf.cmd_grill(mock_update, mock_context))
    check("/grill-me guide sent when no args", "需求深度盤點" in mock_msg.reply_text.call_args[0][0])

    # /goal without args
    mock_msg.text = "/goal"
    asyncio.run(wf.cmd_goal(mock_update, mock_context))
    check("/goal guide sent when no args", "自主目標攻堅模式" in mock_msg.reply_text.call_args[0][0])

    # /boost without args
    mock_msg.text = "/boost"
    asyncio.run(wf.cmd_boost(mock_update, mock_context))
    check("/boost guide sent when no args", "深度推理" in mock_msg.reply_text.call_args[0][0])

    # /teamwork without args
    mock_msg.text = "/teamwork"
    asyncio.run(wf.cmd_teamwork(mock_update, mock_context))
    check("/teamwork guide sent when no args", "多 Agent 協同團隊" in mock_msg.reply_text.call_args[0][0])

    # /browser without args
    mock_msg.text = "/browser"
    asyncio.run(wf.cmd_browser(mock_update, mock_context))
    check("/browser guide sent when no args", "網頁檢索" in mock_msg.reply_text.call_args[0][0])

    # /btw without args
    mock_msg.text = "/btw"
    asyncio.run(wf.cmd_btw(mock_update, mock_context))
    check("/btw guide sent when no args", "背景獨立插問" in mock_msg.reply_text.call_args[0][0])


def test_diff_execution():
    print("=== /diff Command Execution ===")
    mock_update = MagicMock()
    mock_msg = MagicMock()
    mock_msg.reply_text = AsyncMock()
    mock_update.message = mock_msg
    mock_context = MagicMock()

    mock_msg.text = "/diff"
    asyncio.run(wf.cmd_diff(mock_update, mock_context))
    reply_call = mock_msg.reply_text.call_args[0][0]
    check("/diff produces git status card", "代碼變更" in reply_call or "工作目錄完全乾淨" in reply_call)


def main():
    test_duration_parsing()
    test_skills_discovery()
    test_command_prompts()
    test_diff_execution()

    print("\n" + (f"ALL PASS ({len(_results)})" if all(_results)
                  else f"FAILURES: {_results.count(False)}/{len(_results)}"))
    return 0 if all(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
