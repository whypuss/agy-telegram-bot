"""
Interactive UI Components and Keyboards for Antigravity Telegram Bot.

Provides:
- Paginated Model Selector Inline Keyboard
- Cancellation & Action Buttons
- Status and Help formatting cards
"""

from typing import List, Tuple
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import (
    OPENCODE_DEFAULT_MODEL,
    OPENCODE_TIMEOUT,
    is_opencode_model,
)

# ---------------------------------------------------------------------------
# Supported Models Registry
# ---------------------------------------------------------------------------

AVAILABLE_MODELS: List[dict] = [
    # Gemini 3.8 Series
    {"id": "Gemini 3.8 Flash (High)", "name": "Gemini 3.8 Flash (High)", "desc": "高思考預算，深度推理"},
    {"id": "Gemini 3.8 Flash (Medium)", "name": "Gemini 3.8 Flash (Med)", "desc": "平衡思考預算 (預設)"},
    {"id": "Gemini 3.8 Flash (Low)", "name": "Gemini 3.8 Flash (Low)", "desc": "低思考預算，極速回覆"},
    # Gemini 3.7 Series
    {"id": "Gemini 3.7 Flash (High)", "name": "Gemini 3.7 Flash (High)", "desc": "高思考預算，深度推理"},
    {"id": "Gemini 3.7 Flash (Medium)", "name": "Gemini 3.7 Flash (Med)", "desc": "平衡思考預算"},
    {"id": "Gemini 3.7 Flash (Low)", "name": "Gemini 3.7 Flash (Low)", "desc": "低思考預算，極速回覆"},
    # Gemini 3.6 / 3.5 / 3.1 Series
    {"id": "Gemini 3.6 Flash (High)", "name": "Gemini 3.6 Flash (High)", "desc": "3.6 Flash 高思考"},
    {"id": "Gemini 3.5 Flash (High)", "name": "Gemini 3.5 Flash (High)", "desc": "3.5 Flash 高思考"},
    {"id": "Gemini 3.1 Pro (High)", "name": "Gemini 3.1 Pro (High)", "desc": "高智慧程式碼與長文本分析"},
    # Claude & Other Series
    {"id": "Claude Sonnet 4.6 (Thinking)", "name": "Claude Sonnet 4.6 (Thinking)", "desc": "強大程式能力與思考鏈"},
    {"id": "Claude Opus 4.6 (Thinking)", "name": "Claude Opus 4.6 (Thinking)", "desc": "最強架構設計與極限推理"},
    {"id": "GPT-OSS 120B (Medium)", "name": "GPT-OSS 120B (Med)", "desc": "開放權重大型開源模型"},
]

PAGE_SIZE = 6


# ---------------------------------------------------------------------------
# Local OpenCode Models Registry (curated, verified working)
# ---------------------------------------------------------------------------

OPENCODE_MODELS: List[dict] = [
    {"id": "sensenova/deepseek-v4-flash", "name": "DeepSeek V4 Flash", "desc": "極速回覆 (預設備援)"},
    {"id": "sensenova/deepseek-v4-pro", "name": "DeepSeek V4 Pro", "desc": "較強推理"},
    {"id": "opencode/muse-spark-1.3-contributor-free", "name": "Muse Spark 1.3", "desc": "免費額度"},
    {"id": "opencode/muse-spark-1.2-contributor-free", "name": "Muse Spark 1.2", "desc": "免費額度"},
    {"id": "opencode/nemotron-3-ultra-free", "name": "Nemotron 3 Ultra Free", "desc": "免費大型模型"},
    {"id": "opencode/nemotron-3.5-lightning-free", "name": "Nemotron 3.5 Lightning Free", "desc": "免費中型模型"},
    {"id": "opencode/mimo-v2.5-free", "name": "Mimo v2.5 Free", "desc": "Mimo 免費版"},
    {"id": "sensenova/kimi-k3", "name": "Kimi K3", "desc": "長文本對話"},
    {"id": "sensenova/glm-5.2", "name": "GLM 5.2", "desc": "通用對話"},
    {"id": "minimax-cn-coding-plan/MiniMax-M2.5", "name": "MiniMax M2.5", "desc": "Coding 方案額度"},
    {"id": "minimax-cn-coding-plan/MiniMax-M2.7", "name": "MiniMax M2.7", "desc": "Coding 方案額度"},
    {"id": "ollama/gemma4-e2b-uncensored", "name": "Gemma4 E2B (本地)", "desc": "本機 Ollama 離線"},
]

_AGY_SECTION_TITLE = "⚡ Antigravity 模型"
_OC_SECTION_TITLE = "💻 本地 OpenCode 模型"


def resolve_model_alias(model_query: str) -> str:
    """
    Resolve user input to canonical model ID if possible.
    Supports partial matches and aliases for both Antigravity models
    ('3.8', 'sonnet', ...) and OpenCode models ('deepseek', 'spark',
    'minimax', 'kimi', 'glm', 'ollama', 'provider/model', ...).
    """
    query = model_query.strip()
    if not query:
        return ""

    # Direct case-insensitive match against id or name (both backends)
    for m in AVAILABLE_MODELS + OPENCODE_MODELS:
        if m["id"].lower() == query.lower() or m["name"].lower() == query.lower():
            return m["id"]

    q_clean = query.lower().replace("-", " ").replace("_", " ")

    # Explicit provider/model ids pass through (e.g. sensenova/kimi-k3)
    if "/" in query:
        for m in OPENCODE_MODELS:
            if m["id"].lower() == query.lower().strip():
                return m["id"]
        for m in OPENCODE_MODELS:
            if q_clean in m["id"].lower():
                return m["id"]
        return query.strip()

    # OpenCode provider keywords (checked before agy aliases to avoid collisions)
    if "spark" in q_clean or "muse" in q_clean:
        if "1.2" in q_clean:
            return "opencode/muse-spark-1.2-contributor-free"
        return "opencode/muse-spark-1.3-contributor-free"

    if "deepseek" in q_clean:
        if "pro" in q_clean or "v4 pro" in q_clean:
            return "sensenova/deepseek-v4-pro"
        return "sensenova/deepseek-v4-flash"

    if "kimi" in q_clean or q_clean.strip() == "k3":
        return "sensenova/kimi-k3"

    if "glm" in q_clean:
        return "sensenova/glm-5.2"

    if "minimax" in q_clean or "m2" in q_clean:
        if "2.7" in q_clean:
            return "minimax-cn-coding-plan/MiniMax-M2.7"
        return "minimax-cn-coding-plan/MiniMax-M2.5"

    if "ollama" in q_clean or "gemma" in q_clean:
        return "ollama/gemma4-e2b-uncensored"

    if "cliproxy" in q_clean:
        return "cliproxy/sensenova-fast"

    if "sensenova" in q_clean and "fast" in q_clean:
        return "sensenova/deepseek-v4-flash"

    # Quick aliases for model versions
    if "3.8" in q_clean:
        if "high" in q_clean:
            return "Gemini 3.8 Flash (High)"
        elif "low" in q_clean:
            return "Gemini 3.8 Flash (Low)"
        else:
            return "Gemini 3.8 Flash (Medium)"

    if "3.7" in q_clean:
        if "med" in q_clean:
            return "Gemini 3.7 Flash (Medium)"
        elif "low" in q_clean:
            return "Gemini 3.7 Flash (Low)"
        else:
            return "Gemini 3.7 Flash (High)"

    if "3.6" in q_clean:
        if "med" in q_clean:
            return "Gemini 3.6 Flash (Medium)"
        elif "low" in q_clean:
            return "Gemini 3.6 Flash (Low)"
        else:
            return "Gemini 3.6 Flash (High)"

    if "3.5" in q_clean:
        return "Gemini 3.5 Flash (High)"

    if "3.1" in q_clean or ("pro" in q_clean and "gemini" in q_clean):
        return "Gemini 3.1 Pro (High)"

    if "opus" in q_clean:
        return "Claude Opus 4.6 (Thinking)"

    if "sonnet" in q_clean:
        return "Claude Sonnet 4.6 (Thinking)"

    if "oss" in q_clean or "gpt" in q_clean:
        return "GPT-OSS 120B (Medium)"

    # Substring match in id or name (both backends, agy first)
    for m in AVAILABLE_MODELS + OPENCODE_MODELS:
        if q_clean in m["id"].lower() or q_clean in m["name"].lower():
            return m["id"]

    # Fallback to verbatim query
    return query


def build_model_keyboard(current_model: str, page: int = 0) -> Tuple[InlineKeyboardMarkup, int, int]:
    """
    Build a paginated inline keyboard for unified model selection.
    Sections: Antigravity models first, then local OpenCode models.
    Selecting an OpenCode model switches the backend to local OpenCode;
    agy-model turns auto-fallback to OpenCode when agy fails.
    Returns (keyboard_markup, current_page, total_pages).
    """
    combined: List[Tuple[str, dict]] = (
        [("agy", m) for m in AVAILABLE_MODELS]
        + [("oc", m) for m in OPENCODE_MODELS]
    )
    oc_start_idx = len(AVAILABLE_MODELS)
    total_pages = max(1, (len(combined) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, len(combined))
    page_items = combined[start_idx:end_idx]

    buttons: List[List[InlineKeyboardButton]] = []

    for offset, (section, m) in enumerate(page_items):
        global_idx = start_idx + offset
        # Section header when a section starts on this page
        if global_idx == 0:
            buttons.append([InlineKeyboardButton(f"── {_AGY_SECTION_TITLE} ──", callback_data="noop")])
        elif global_idx == oc_start_idx:
            buttons.append([InlineKeyboardButton(f"── {_OC_SECTION_TITLE} ──", callback_data="noop")])

        model_id = m["id"]
        is_active = (model_id.lower() == (current_model or "").lower())
        prefix = "✓ " if is_active else ""
        suffix = "" if section == "agy" else " (OC)"
        button_text = f"{prefix}{m['name']}{suffix}"
        buttons.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"set_model:{model_id}"
            )
        ])

    # Navigation row
    nav_row: List[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ 上一頁", callback_data=f"page_model:{page - 1}"))
    
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="noop"))
    
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("下一頁 ➡️", callback_data=f"page_model:{page + 1}"))

    buttons.append(nav_row)

    # Action row (Close)
    buttons.append([
        InlineKeyboardButton("❌ 關閉選單", callback_data="close_model_picker")
    ])

    return InlineKeyboardMarkup(buttons), page, total_pages


def build_cancel_keyboard() -> InlineKeyboardMarkup:
    """Build an inline keyboard with a Cancel button for ongoing tasks."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 中止執行 (Cancel)", callback_data="cancel_task")]
    ])


def format_usage_card(
    user_id: int,
    user_name: str,
    current_model: str,
    conversation_id: str | None,
    usage_summary: dict,
) -> str:
    """Format a rich token & resource usage report card in standard Markdown."""
    session = usage_summary.get("session") or {}
    last_turn = usage_summary.get("last_turn") or {}
    lifetime = usage_summary.get("lifetime") or {}

    conv_display = f"`{conversation_id}`" if conversation_id else "*（無活躍會話）*"

    # Format session stats
    session_turns = session.get("num_turns", 0)
    session_total = session.get("total_tokens", 0)
    session_input = session.get("input_tokens", 0)
    session_output = session.get("output_tokens", 0)
    session_thinking = session.get("thinking_tokens", 0)
    session_cache = session.get("cache_read_tokens", 0)

    # Format last turn stats
    last_total = last_turn.get("total_tokens", 0)
    last_input = last_turn.get("input_tokens", 0)
    last_output = last_turn.get("output_tokens", 0)
    last_thinking = last_turn.get("thinking_tokens", 0)
    last_duration = last_turn.get("duration_seconds", 0.0)

    # Format lifetime stats
    life_turns = lifetime.get("turns", 0)
    life_total = lifetime.get("total_tokens", 0)
    life_input = lifetime.get("input_tokens", 0)
    life_output = lifetime.get("output_tokens", 0)

    lines = [
        "📊 **Antigravity Token 用量與資源統計**\n",
        f"👤 **使用者**: {user_name} (`{user_id}`)",
        f"🧠 **當前模型**: `{current_model}`",
        f"🔌 **後端**: {'💻 本地 OpenCode' if is_opencode_model(current_model) else '⚡ Antigravity'}",
        f"💬 **會話 ID**: {conv_display}\n",
        "🔹 **當前會話用量 (Current Session):**",
    ]

    if session_total > 0:
        lines.extend([
            f"• 會話輪次: `{session_turns}` 輪",
            f"• 總消耗: `{session_total:,}` tokens",
            f"  ├ 📥 提示詞 (Input): `{session_input:,}`",
            f"  ├ 📤 模型輸出 (Output): `{session_output:,}`",
            f"  ├ 🧠 深度思考 (Thinking): `{session_thinking:,}`",
            f"  └ ⚡ 快取命中 (Cache Read): `{session_cache:,}`",
        ])
    else:
        lines.append("• 尚未開始對話或已重置會話記憶")

    lines.append("\n🔹 **最近單輪消耗 (Last Turn):**")
    if last_total > 0:
        lines.extend([
            f"• 本輪總計: `{last_total:,}` tokens (耗時 {last_duration:.1f}s)",
            f"  ├ 📥 提示詞: `{last_input:,}`",
            f"  ├ 📤 輸出: `{last_output:,}`",
            f"  └ 🧠 思考: `{last_thinking:,}`",
        ])
    else:
        lines.append("• 尚無最近單輪記錄")

    lines.append("\n🔹 **全域累計統計 (Since Bot Startup):**")
    lines.extend([
        f"• 總請求輪次: `{life_turns}` 輪",
        f"• 累計總消耗: `{life_total:,}` tokens",
    ])
    if life_total > 0:
        lines.extend([
            f"  ├ 📥 提示詞: `{life_input:,}`",
            f"  └ 📤 模型輸出: `{life_output:,}`",
        ])

    lines.append("\n💡 *提示：使用 /reset 可開啟新對話並重置會話計數，每輪對話底部亦會顯示即時用量。*")

    return "\n".join(lines)


def format_status_card(
    user_id: int,
    user_name: str,
    current_model: str,
    conversation_id: str | None,
    uptime_str: str,
    is_running: bool,
    workspace_dir: str,
    proxy_url: str | None = None,
    usage_stats: dict | None = None,
    oc_session_id: str | None = None,
    oc_model: str | None = None,
) -> str:
    """Format a rich status overview in standard Markdown."""
    status_icon = "🟢 正在運行任務..." if is_running else "⚪ 空閒中 (Idle)"
    conv_display = f"`{conversation_id}`" if conversation_id else "*（尚未建立，傳送訊息將開啟）*"
    proxy_display = f"`{proxy_url}`" if proxy_url else "無代理 (Direct Connection)"
    backend_label = "💻 本地 OpenCode" if is_opencode_model(current_model) else "⚡ Antigravity"
    oc_sess_display = f"`{oc_session_id}`" if oc_session_id else "（無）"

    session = (usage_stats or {}).get("session") or {}
    total_tokens = session.get("total_tokens", 0)
    turns = session.get("num_turns", 0)

    if total_tokens > 0:
        usage_line = f"📊 **Token 用量**: `{total_tokens:,}` tokens (當前會話 {turns} 輪)\n"
    else:
        usage_line = "📊 **Token 用量**: `0` tokens\n"

    return (
        f"📊 **Antigravity Agent 狀態報告**\n\n"
        f"👤 **使用者**: {user_name} (`{user_id}`)\n"
        f"⚡ **運作狀態**: {status_icon}\n"
        f"🔌 **當前後端**: {backend_label}\n"
        f"🧠 **當前模型**: `{current_model}`\n"
        f"💬 **AGY 會話 ID**: {conv_display}\n"
        f"💻 **OC 會話 ID**: {oc_sess_display}\n"
        f"🔁 **自動備援**: {fallback_display}\n"
        f"{usage_line}"
        f"📂 **工作目錄**: `{workspace_dir}`\n"
        f"🌐 **網路代理**: {proxy_display}\n"
        f"⏱️ **Bot 上線時長**: {uptime_str}\n\n"
        f"💡 *提示：使用 /model 切換模型（含 OpenCode 模型），使用 /usage 查看詳細 Token 用量，使用 /reset 重置會話記憶。*"
    )


def format_help_card(current_model: str, timeout_seconds: int) -> str:
    """Format a rich help documentation card in standard Markdown."""
    return (
        "🤖 **Antigravity Telegram Bot 指南**\n\n"
        "你可以透過此 Bot 隨時隨地遠端調度你的 Antigravity AI Agent。\n"
        "Agent 具備完整的程式碼編寫、終端指令執行、文件讀寫與多模態分析能力。\n\n"
        "🎯 **常用指令：**\n"
        "• `/usage` — 📊 查看 Token 用量與資源消耗統計\n"
        "• `/model` — 🧠 點擊按鈕互動式切換 AI 模型（含 💻 本地 OpenCode 模型，選 OC 模型即切換後端）\n"
        "• `/model <名稱>` — ⌨️ 直接切換，例如 `/model deepseek`、`/model spark`、`/model 3.8`\n"
        "• `/memory` — 🧠 查看與管理本機持久記憶 (MEMORY.md / USER.md)\n"
        "• `/compact` — 📦 壓縮當前上下文（瘦身並保留關鍵記憶）\n"
        "• `/reset` 或 `/new` — 🔄 開啟全新對話會話\n"
        "• `/status` — 📈 查看目前 Agent 狀態與會話資訊\n"
        "• `/cancel` — 🛑 中止正在執行的耗時任務\n"
        "• `/steer <文字>` — ⚡ 中止當前任務並立即發送修正指示（Hermes 風格）\n"
        "• `/clear` — 🧹 清理暫存的多模態快取檔案\n"
        "• `/help` — 📖 顯示此說明卡片\n\n"
        "📬 **任務進行中輸入新訊息：**\n"
        "• 新訊息會自動**排入佇列**，待當前任務結束後依序處理，不會浪費 Token\n"
        "• 欲立即修正，請用 `/steer <修正內容>` 中止當前任務後重發\n\n"
        "📸 **多模態支援：**\n"
        "• 傳送 **圖片/相簿**：自動下載並交由視覺模型分析\n"
        "• 傳送 **語音訊息**：自動下載語音進行聽覺與語音理解\n"
        "• 傳送 **檔案/文件**：自動存入暫存目錄並提供給 Agent 閱讀\n\n"
        f"⚙️ **目前預設模型：** `{current_model}`\n"
        f"⏳ **單次執行超時：** {timeout_seconds} 秒"
    )

