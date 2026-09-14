"""Smoke tests for status/usage cards and markdown helpers. Run: venv/bin/python3 -m test_status_cards"""
from formatter import format_markdown_v2, split_markdown_chunks, strip_markdown_v2, utf16_len

_results = []


def check(name, cond, detail=""):
    _results.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail and not cond else ''}")


def main():
    from ui_components import format_status_card, format_usage_card

    print("=== /status card (NameError regression) ===")
    card = format_status_card(
        user_id=1120349178,
        user_name="Tester",
        current_model="Gemini 3.8 Flash (Low)",
        conversation_id="123e4567-e89b-12d3-a456-426614174000",
        uptime_str="1小時 2分 3秒",
        is_running=True,
        workspace_dir="/Users/whypuss",
        proxy_url=None,
        usage_stats={"session": {"total_tokens": 1234, "num_turns": 2}},
        oc_session_id=None,
        oc_model=None,
    )
    check("returns text", isinstance(card, str) and len(card) > 50)
    check("shows running state", "正在運行任務" in card)
    check("shows fallback line", "自動備援" in card)

    print("=== /usage card ===")
    usage = format_usage_card(
        user_id=1,
        user_name="Tester",
        current_model="Gemini 3.8 Flash (Low)",
        conversation_id=None,
        usage_summary={},
    )
    check("returns text", isinstance(usage, str) and "Token" in usage)

    print("=== cards survive MarkdownV2 conversion ===")
    for name, text in [("status", card), ("usage", usage)]:
        formatted = format_markdown_v2(text)
        chunks = split_markdown_chunks(formatted)
        check(f"{name} formats and chunks", all(utf16_len(c) <= 4096 for c in chunks))
        check(f"{name} round-trips", strip_markdown_v2(formatted))

    print("=== memory overview card (literal-marker regression) ===")
    overview = (
        "🧠 **本機持久化記憶管理 (Hermes Architecture)**\n"
        "📂 儲存路徑：`/Users/whypuss/.hermes/memories`\n\n"
        "👤 **用戶偏好與規則 (USER.md)**：共 `3` 條"
    )
    formatted = format_markdown_v2(overview)
    check("bold converted to MDV2 single-star", "*用戶偏好與規則" in formatted.replace("\\", ""))


def _summary():
    passed = sum(_results)
    print(f"\n{passed}/{len(_results)} checks passed")
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    import sys
    main()
    sys.exit(_summary())
