"""Tests for OpenCode execution engine optimizations."""
import unittest
import asyncio
from opencode_runner import (
    _parse_tool_to_indicator,
    get_user_oc_thinking,
    set_user_oc_thinking,
)

_results = []

def check(name, cond, detail=""):
    _results.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail and not cond else ''}")


def test_tool_indicator_parsing():
    print("=== OpenCode Rich Tool Indicator Parsing ===")
    
    # 1. Bash command active & completed
    bash_part = {
        "tool": "bash",
        "state": {
            "status": "running",
            "input": {"command": "git status --short"},
        }
    }
    ind = _parse_tool_to_indicator(bash_part, active=True)
    check("bash active shows command", "`git status --short`" in ind and "▶ 🔧" in ind)

    bash_part_done = {
        "tool": "bash",
        "state": {
            "status": "completed",
            "input": {"command": "git status --short"},
            "time": {"start": 1000, "end": 1450},
        }
    }
    ind_done = _parse_tool_to_indicator(bash_part_done, active=False)
    check("bash completed shows duration", "✓ 🔧" in ind_done and "(0.5s)" in ind_done)

    # 2. File read & write
    read_part = {
        "tool": "read",
        "state": {
            "status": "completed",
            "input": {"path": "/Users/whypuss/projects/test/main.py"},
            "time": {"start": 1000, "end": 1200},
        }
    }
    ind_read = _parse_tool_to_indicator(read_part, active=False)
    check("read shows basename", "`main.py`" in ind_read and "讀取檔案" in ind_read)

    # 3. Backward compatibility with plain string
    ind_legacy = _parse_tool_to_indicator("bash", active=True)
    check("legacy string tool input works", "執行終端指令" in ind_legacy and "▶ 🔧" in ind_legacy)


def test_opencode_thinking_store():
    print("=== OpenCode Thinking Process Store & Synthesis ===")
    uid = 999123
    set_user_oc_thinking(uid, None)
    check("initially empty", get_user_oc_thinking(uid) is None)

    mock_th = "【需求分析與規劃】\n確認當前環境設定\n\n【關鍵發現與進展】\n找到目標配置檔案\n\n【推演結論與行動】\n更新配置並進行重啟"
    set_user_oc_thinking(uid, mock_th)
    check("thinking stored correctly", get_user_oc_thinking(uid) == mock_th)

    set_user_oc_thinking(uid, None)
    check("thinking cleared", get_user_oc_thinking(uid) is None)


def main():
    test_tool_indicator_parsing()
    test_opencode_thinking_store()
    total = len(_results)
    passed = sum(_results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
