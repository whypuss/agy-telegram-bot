"""
Interactive UI Components and Keyboards for Antigravity Telegram Bot.

Provides:
- Paginated Model Selector Inline Keyboard
- Cancellation & Action Buttons
- Status and Help formatting cards
"""

from typing import List, Tuple
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# ---------------------------------------------------------------------------
# Supported Models Registry
# ---------------------------------------------------------------------------

AVAILABLE_MODELS: List[dict] = [
    # Gemini Series
    {"id": "Gemini 3.7 Flash (High)", "name": "Gemini 3.7 Flash (High)", "desc": "高思考預算，深度推理 (預設)"},
    {"id": "Gemini 3.7 Flash (Medium)", "name": "Gemini 3.7 Flash (Med)", "desc": "平衡思考預算"},
    {"id": "Gemini 3.7 Flash (Low)", "name": "Gemini 3.7 Flash (Low)", "desc": "低思考預算，極速回覆"},
    {"id": "Gemini 3.6 Flash (High)", "name": "Gemini 3.6 Flash (High)", "desc": "3.6 Flash 高思考"},
    {"id": "Gemini 3.5 Flash (High)", "name": "Gemini 3.5 Flash (High)", "desc": "3.5 Flash 高思考"},
    {"id": "Gemini 3.1 Pro (High)", "name": "Gemini 3.1 Pro (High)", "desc": "高智慧程式碼與長文本分析"},
    # Claude & Other Series
    {"id": "Claude Sonnet 4.6 (Thinking)", "name": "Claude Sonnet 4.6 (Thinking)", "desc": "強大程式能力與思考鏈"},
    {"id": "Claude Opus 4.6 (Thinking)", "name": "Claude Opus 4.6 (Thinking)", "desc": "最強架構設計與極限推理"},
    {"id": "GPT-OSS 120B (Medium)", "name": "GPT-OSS 120B (Med)", "desc": "開放權重大型開源模型"},
]

PAGE_SIZE = 5


def build_model_keyboard(current_model: str, page: int = 0) -> Tuple[InlineKeyboardMarkup, int, int]:
    """
    Build a paginated inline keyboard for model selection.
    Returns (keyboard_markup, current_page, total_pages).
    """
    total_models = len(AVAILABLE_MODELS)
    total_pages = (total_models + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))

    start_idx = page * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, total_models)
    page_models = AVAILABLE_MODELS[start_idx:end_idx]

    buttons: List[List[InlineKeyboardButton]] = []

    # Model rows
    for m in page_models:
        model_id = m["id"]
        is_active = (model_id.lower() == current_model.lower())
        prefix = "✓ " if is_active else ""
        button_text = f"{prefix}{m['name']}"
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


def format_status_card(
    user_id: int,
    user_name: str,
    current_model: str,
    conversation_id: str | None,
    uptime_str: str,
    is_running: bool,
    workspace_dir: str,
    proxy_url: str | None = None,
) -> str:
    """Format a rich status overview."""
    status_icon = "🟢 正在運行任務..." if is_running else "⚪ 空閒中 (Idle)"
    conv_display = f"`{conversation_id}`" if conversation_id else "（尚未建立，傳送訊息將開啟）"
    proxy_display = f"`{proxy_url}`" if proxy_url else "無代理 (Direct Connection)"

    return (
        f"📊 *Antigravity Agent 狀態報告*\n\n"
        f"👤 *使用者*: {user_name} \\(`{user_id}`\\)\n"
        f"⚡ *運作狀態*: {status_icon}\n"
        f"🧠 *當前模型*: `{current_model}`\n"
        f"💬 *會話 ID*: {conv_display}\n"
        f"📂 *工作目錄*: `{workspace_dir}`\n"
        f"🌐 *網路代理*: {proxy_display}\n"
        f"⏱️ *Bot 上線時長*: {uptime_str}\n\n"
        f"💡 _提示：使用 /model 切換模型，使用 /reset 重置會話記憶。_"
    )


def format_help_card(current_model: str, timeout_seconds: int) -> str:
    """Format a rich help documentation card."""
    return (
        "🤖 *Antigravity Telegram Bot 指南*\n\n"
        "你可以透過此 Bot 隨時隨地遠端調度你的 Antigravity AI Agent。\n"
        "Agent 具備完整的程式碼編寫、終端指令執行、文件讀寫與多模態分析能力。\n\n"
        "🎯 *常用指令：*\n"
        "• `/model` — 點擊按鈕互動式切換 AI 模型\n"
        "• `/reset` 或 `/new` — 開啟全新對話會話\n"
        "• `/status` — 查看目前 Agent 狀態與會話資訊\n"
        "• `/cancel` — 中止正在執行的耗時任務\n"
        "• `/clear` — 清理暫存的多模態快取檔案\n"
        "• `/help` — 顯示此說明卡片\n\n"
        "📸 *多模態支援：*\n"
        "• 傳送 **圖片/相簿**：自動下載並交由 Gemini 視覺模型分析\n"
        "• 傳送 **語音訊息**：自動下載語音進行聽覺與語音理解\n"
        "• 傳送 **檔案/文件**：自動存入暫存目錄並提供給 Agent 閱讀\n\n"
        f"⚙️ *目前預設模型：* `{current_model}`\n"
        f"⏳ *單次執行超時：* {timeout_seconds} 秒"
    )
