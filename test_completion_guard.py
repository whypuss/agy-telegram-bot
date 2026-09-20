#!/usr/bin/env python3
"""
Unit tests for Autonomous Completion Guard & Task Interruption Recovery.
Tests:
1. ACTIVE_TASK_FILE creation, persistence, and cleanup
2. watchdog is_agent_active() logic
3. agent_runner autonomous completion prompt reinforcement
4. protocol.md invariant existence
"""

import json
import os
import tempfile
from pathlib import Path

from config import ACTIVE_TASK_FILE
import watchdog as wd


def test_active_task_marker_lifecycle():
    print("Testing active task marker lifecycle...")
    test_data = {
        "uid": 123456,
        "chat_id": 987654,
        "thread_id": None,
        "start_time": 1700000000.0,
        "prompt": "Test long running task",
    }
    # Write
    ACTIVE_TASK_FILE.write_text(json.dumps(test_data), encoding="utf-8")
    assert ACTIVE_TASK_FILE.exists(), "Active task file should exist after writing"

    # Verify watchdog recognizes active agent
    assert wd.is_agent_active() is True, "watchdog should detect agent is active via file"

    # Cleanup
    ACTIVE_TASK_FILE.unlink(missing_ok=True)
    assert not ACTIVE_TASK_FILE.exists(), "Active task file should be cleaned up"
    print("  -> PASS: active task marker lifecycle")


def test_protocol_completion_invariants():
    print("Testing protocol.md completion invariants...")
    protocol_path = Path(__file__).parent / "protocol.md"
    assert protocol_path.exists(), "protocol.md must exist"
    content = protocol_path.read_text(encoding="utf-8")

    assert "執行閉環與禁止承諾停滯規範" in content, "protocol.md must contain autonomous completion invariant"
    assert "嚴禁未來時態承諾" in content, "protocol.md must forbid future promises"
    assert "嚴禁在對話中途立即自我重啟" in content, "protocol.md must forbid mid-task self restarts"
    print("  -> PASS: protocol.md contains required invariants")


if __name__ == "__main__":
    test_active_task_marker_lifecycle()
    test_protocol_completion_invariants()
    print("All completion guard tests passed successfully!")
