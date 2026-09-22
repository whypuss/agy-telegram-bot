"""Unit tests for Antigravity IDE CDP remote control integration."""
import json
from ui_components import (
    build_backend_keyboard,
    build_question_keyboard,
    format_status_card,
)
from session_store import (
    get_user_backend,
    set_user_backend,
    user_backends,
)
from ide_cdp import (
    PENDING_ACTION_TEXTS,
    SUBMIT_ACTION_TEXTS,
    IDE_LOCATORS_JS,
)
from config import IDE_CDP_PORT, IDE_CDP_HOST

_results = []


def check(name, cond, detail=""):
    _results.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail and not cond else ''}")


def main():
    print("=== IDE CDP Configuration & Locators ===")
    check("IDE_CDP_PORT defaults to 9334", IDE_CDP_PORT == 9334)
    check("IDE_CDP_HOST defaults to 127.0.0.1", IDE_CDP_HOST == "127.0.0.1")
    check("PENDING_ACTION_TEXTS contains common accept words", "run" in PENDING_ACTION_TEXTS and "accept" in PENDING_ACTION_TEXTS)
    check("SUBMIT_ACTION_TEXTS contains common submit words", "submit" in SUBMIT_ACTION_TEXTS and "send" in SUBMIT_ACTION_TEXTS)
    check("IDE_LOCATORS_JS defines AG_UI object", "var AG_UI = {" in IDE_LOCATORS_JS)
    check("IDE_LOCATORS_JS contains getChatInput", "getChatInput" in IDE_LOCATORS_JS)
    check("IDE_LOCATORS_JS contains getStopButton", "getStopButton" in IDE_LOCATORS_JS)
    check("IDE_LOCATORS_JS contains checkForQuestion", "checkForQuestion" in IDE_LOCATORS_JS)
    check("IDE_LOCATORS_JS contains clickPendingActionButtons", "clickPendingActionButtons" in IDE_LOCATORS_JS)

    print("\n=== Backend Store Management ===")
    test_uid = 987654321
    orig_backend = get_user_backend(test_uid)
    check("default backend returns agy or configured default", orig_backend in ("ide", "agy", "opencode"))
    
    set_user_backend(test_uid, "ide")
    check("set_user_backend sets to ide", get_user_backend(test_uid) == "ide")
    
    set_user_backend(test_uid, "opencode")
    check("set_user_backend sets to opencode", get_user_backend(test_uid) == "opencode")
    
    set_user_backend(test_uid, "agy")
    check("set_user_backend sets to agy", get_user_backend(test_uid) == "agy")
    user_backends.pop(test_uid, None)

    print("\n=== UI Components & Keyboards ===")
    kb_backend = build_backend_keyboard("ide")
    check("build_backend_keyboard generates inline keyboard", hasattr(kb_backend, "inline_keyboard"))
    check("keyboard contains ide, agy, opencode buttons", len(kb_backend.inline_keyboard) == 4)
    
    q_data = {
        "header": "請問您希望如何實作？",
        "options": ["使用 React", "使用 Vue", "使用 Vanilla JS"],
    }
    kb_q = build_question_keyboard(q_data)
    check("build_question_keyboard creates buttons for options", len(kb_q.inline_keyboard) == 3)
    check("option callback_data matches ide_ans:1", kb_q.inline_keyboard[0][0].callback_data == "ide_ans:1")

    kb_q_empty = build_question_keyboard({"header": "確認繼續？", "options": []})
    check("empty options prompt creates confirm/skip buttons", len(kb_q_empty.inline_keyboard) == 1 and len(kb_q_empty.inline_keyboard[0]) == 2)

    from ui_components import build_ide_model_keyboard
    models = ["Gemini 3.8 Flash Medium", "Claude Sonnet 4.6 (Thinking)"]
    kb_m = build_ide_model_keyboard(models, "Gemini 3.8 Flash Medium")
    check("build_ide_model_keyboard marks active model with checkmark", "✓" in kb_m.inline_keyboard[0][0].text)
    check("build_ide_model_keyboard creates callback_data with model name", kb_m.inline_keyboard[0][0].callback_data == "ide_model_set:Gemini 3.8 Flash Medium")


    print("\n=== Status Card with IDE Backend ===")
    status_ide = format_status_card(
        user_id=123,
        user_name="Tester",
        current_model="Gemini 3.8 Flash (Medium)",
        conversation_id="conv-123",
        uptime_str="1小時 10分",
        is_running=False,
        workspace_dir="/tmp",
        active_backend="ide",
        ide_port=9334,
        ide_online=True,
    )
    check("status card mentions Antigravity IDE (CDP)", "Antigravity IDE (CDP 埠 9334" in status_ide)
    check("status card shows online state", "線上" in status_ide)

    print("\n" + (f"ALL PASS ({len(_results)})" if all(_results)
                  else f"FAILURES: {_results.count(False)}/{len(_results)}"))
    return 0 if all(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
