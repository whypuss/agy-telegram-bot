"""
Antigravity Telegram Bot — Main Application
=============================================
A robust, production-grade Telegram Bot interface for Google Antigravity Agent & Gemini,
featuring Hermes-style network resilience, multimodal media handling, code-block aware
Markdown formatting, message batching, and interactive UI.
"""

import asyncio
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from telegram import (
    Update,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    BotCommand,
)
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import BadRequest, NetworkError, TimedOut, Conflict

from config import (
    TELEGRAM_BOT_TOKEN,
    ALLOWED_USER_IDS,
    PROXY_URL,
    REQUIRE_MENTION_IN_GROUPS,
    AGY_TIMEOUT,
    DEFAULT_MODEL,
    WORKSPACE_DIR,
    TEXT_BATCH_DELAY_SECONDS,
    MEDIA_BATCH_DELAY_SECONDS,
    CACHE_DIR,
    is_authorized,
)
from formatter import (
    format_markdown_v2,
    strip_markdown_v2,
    split_markdown_chunks,
    utf16_len,
)
from media_handler import (
    save_photo,
    save_voice,
    save_audio,
    save_document,
    cleanup_cache,
    detect_local_files_in_response,
)
from agent_runner import (
    run_agent_turn,
    compact_user_conversation,
    get_user_model,
    set_user_model,
    get_user_conversation,
    reset_user_conversation,
    get_user_usage_summary,
    is_user_task_running,
    cancel_user_task,
)
from ui_components import (
    AVAILABLE_MODELS,
    build_model_keyboard,
    build_cancel_keyboard,
    format_status_card,
    format_usage_card,
    format_help_card,
    resolve_model_alias,
)

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("agy-tg-bot")

# Track startup timestamp for uptime calculation
BOT_START_TIME = time.time()


def get_uptime_string() -> str:
    """Format bot uptime as a human-readable string."""
    seconds = int(time.time() - BOT_START_TIME)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days > 0:
        return f"{days}天 {hours}小時 {minutes}分"
    if hours > 0:
        return f"{hours}小時 {minutes}分 {seconds}秒"
    return f"{minutes}分 {seconds}秒"


def format_elapsed(seconds: int) -> str:
    """Format elapsed seconds."""
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    return f"{m}m{s}s"


# ---------------------------------------------------------------------------
# Inbound Message & Photo Batching
# ---------------------------------------------------------------------------
# Buffer rapid text chunks (e.g. when Telegram client splits a long pasted message)
_pending_text_batches: Dict[int, dict] = {}
_pending_text_tasks: Dict[int, asyncio.Task] = {}

# Buffer photo albums or multi-photo bursts
_pending_photo_batches: Dict[str, dict] = {}
_pending_photo_tasks: Dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# Message Sending with Fallback
# ---------------------------------------------------------------------------

async def send_formatted_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_to_message_id: Optional[int] = None,
    thread_id: Optional[int] = None,
) -> None:
    """
    Send text reply with MarkdownV2 formatting, chunk splitting,
    and automatic plain text fallback if parsing fails.
    """
    if not text or not text.strip():
        return

    chat_id = update.effective_chat.id
    formatted_text = format_markdown_v2(text)
    chunks = split_markdown_chunks(formatted_text)

    for chunk in chunks:
        # Try MarkdownV2 first
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=chunk,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_to_message_id=reply_to_message_id,
                message_thread_id=thread_id,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except BadRequest as e:
            # Fallback to plain text on Markdown syntax parse errors
            if "parse" in str(e).lower() or "entities" in str(e).lower():
                logger.warning("MarkdownV2 parse failed (%s), falling back to plain text", e)
                plain_chunk = strip_markdown_v2(chunk)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=plain_chunk,
                    reply_to_message_id=reply_to_message_id,
                    message_thread_id=thread_id,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            else:
                raise


async def send_detected_media(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    response_text: str,
    thread_id: Optional[int] = None,
) -> None:
    """Detect and send generated local images/documents referenced in response."""
    media_items = detect_local_files_in_response(response_text)
    chat_id = update.effective_chat.id

    for media_type, file_path in media_items:
        try:
            if media_type == "image":
                with open(file_path, "rb") as photo_file:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo_file,
                        caption=f"🖼️ `{Path(file_path).name}`",
                        parse_mode=ParseMode.MARKDOWN_V2,
                        message_thread_id=thread_id,
                    )
            elif media_type == "document":
                with open(file_path, "rb") as doc_file:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=doc_file,
                        caption=f"📁 `{Path(file_path).name}`",
                        parse_mode=ParseMode.MARKDOWN_V2,
                        message_thread_id=thread_id,
                    )
        except Exception as e:
            logger.warning("Failed to send detected media %s: %s", file_path, e)


# ---------------------------------------------------------------------------
# Core Turn Processing
# ---------------------------------------------------------------------------

async def process_agent_turn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prompt: str,
    original_message_id: int,
) -> None:
    """Execute a turn with continuous typing, live progress edits, and response delivery."""
    user = update.effective_user
    uid = user.id
    chat = update.effective_chat
    thread_id = getattr(update.message, "message_thread_id", None) if update.message else None

    current_model = get_user_model(uid)

    # Send initial status message
    status_msg = await context.bot.send_message(
        chat_id=chat.id,
        text=f"⏳ *已接收請求，Agent 啟動中\\.\\.\\.*\n📌 模型: `{format_markdown_v2(current_model)}`",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_to_message_id=original_message_id,
        message_thread_id=thread_id,
        reply_markup=build_cancel_keyboard(),
    )

    start_time = time.time()
    progress_items: List[str] = []
    last_status_update = [start_time]

    # Continuous typing heartbeat
    typing_active = True

    async def _typing_heartbeat():
        while typing_active:
            try:
                await context.bot.send_chat_action(
                    chat_id=chat.id,
                    action=ChatAction.TYPING,
                    message_thread_id=thread_id,
                )
            except Exception:
                pass
            await asyncio.sleep(4.0)

    typing_task = asyncio.create_task(_typing_heartbeat())

    # Progress line callback
    async def on_progress(indicator: str):
        # In-place replacement: if previous item was active and new indicator completes it, update in-place
        if progress_items and progress_items[-1].startswith("▶ ") and indicator.startswith("✓ "):
            progress_items[-1] = indicator
        elif progress_items and progress_items[-1].startswith("▶ ") and indicator.startswith("▶ "):
            progress_items[-1] = indicator
        else:
            progress_items.append(indicator)

        while len(progress_items) > 8:
            progress_items.pop(0)

        now = time.time()
        # Rate limit status edits to once every 1.5 seconds
        if now - last_status_update[0] >= 1.5:
            last_status_update[0] = now
            elapsed = format_elapsed(int(now - start_time))
            progress_block = "\n".join(progress_items)
            msg_text = (
                f"⏳ *Agent 處理中\\.\\.\\.* \\({elapsed}\\)\n"
                f"📌 模型: `{format_markdown_v2(current_model)}`\n\n"
                f"📋 *執行過程：*\n"
                f"{format_markdown_v2(progress_block)}"
            )
            try:
                await status_msg.edit_text(
                    text=msg_text,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=build_cancel_keyboard(),
                )
            except Exception:
                # Fallback to plain text on Markdown edit error
                try:
                    await status_msg.edit_text(
                        text=f"⏳ Agent 處理中... ({elapsed})\n📌 模型: {current_model}\n\n📋 執行過程：\n{progress_block}",
                        reply_markup=build_cancel_keyboard(),
                    )
                except Exception:
                    pass

    # Run agent execution
    try:
        reply_text, new_conv_id, turn_usage = await run_agent_turn(
            prompt=prompt,
            user_id=uid,
            on_progress=on_progress,
        )
    except asyncio.CancelledError:
        reply_text = "🛑 任務已由使用者中止。"
        turn_usage = None
    except asyncio.TimeoutError:
        reply_text = f"⏰ Agent 執行超時（{AGY_TIMEOUT} 秒）。\n請簡化需求或使用 /reset 重置會話。"
        turn_usage = None
    except Exception as e:
        logger.exception("Error executing turn for user %s: %s", uid, e)
        reply_text = f"❌ 執行發生未預期錯誤：\n```\n{e}\n```"
        turn_usage = None
    finally:
        typing_active = False
        typing_task.cancel()

    # Update status message to Completed with token metrics & preserved process trace
    elapsed_total = format_elapsed(int(time.time() - start_time))
    token_str = ""
    if turn_usage and turn_usage.get("total_tokens"):
        tokens = turn_usage["total_tokens"]
        token_str = f" · {tokens:,} tokens"

    final_items = []
    for it in progress_items:
        if it.startswith("▶ "):
            final_items.append(it.replace("▶ ", "✓ "))
        else:
            final_items.append(it)

    if final_items:
        completed_block = "\n".join(final_items)
        final_status_text = (
            f"✅ *任務完成* \\(耗時 {elapsed_total}{format_markdown_v2(token_str)}\\)\n"
            f"📌 模型: `{format_markdown_v2(current_model)}`\n\n"
            f"📋 *執行過程：*\n"
            f"{format_markdown_v2(completed_block)}"
        )
        plain_final_text = (
            f"✅ 任務完成 (耗時 {elapsed_total}{token_str})\n"
            f"📌 模型: {current_model}\n\n"
            f"📋 執行過程：\n"
            f"{completed_block}"
        )
    else:
        final_status_text = f"✅ *任務完成* \\(耗時 {elapsed_total}{format_markdown_v2(token_str)}\\)"
        plain_final_text = f"✅ 任務完成 (耗時 {elapsed_total}{token_str})"

    try:
        await status_msg.edit_text(
            text=final_status_text,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=None,
        )
    except Exception:
        try:
            await status_msg.edit_text(
                text=plain_final_text,
                reply_markup=None,
            )
        except Exception:
            pass

    # Append usage footer directly to the reply text if available
    final_reply_text = reply_text
    if turn_usage and turn_usage.get("total_tokens"):
        tokens = turn_usage["total_tokens"]
        in_tok = turn_usage.get("input_tokens", 0)
        out_tok = turn_usage.get("output_tokens", 0)
        dur = turn_usage.get("duration_seconds")
        # Ensure single turn duration is displayed rather than lifetime
        if not dur or dur > 3600:
            dur = max(0.1, time.time() - start_time)
        usage_footer = f"\n\n---\n⏱️ `{dur:.1f}s` · 📊 `{tokens:,}` tokens (📥 `{in_tok:,}` / 📤 `{out_tok:,}`)"
        
        # High token warning
        user_usage = get_user_usage_summary(uid)
        session_tok = ((user_usage or {}).get("session") or {}).get("total_tokens", 0)
        if session_tok > 180000:
            usage_footer += f"\n💡 *提示: 當前會話累積達 `{session_tok:,}` tokens，建議執行 /compact 壓縮上下文以維持最佳反應速度。*"

        final_reply_text = f"{reply_text}{usage_footer}"

    # Send response text
    await send_formatted_reply(
        update=update,
        context=context,
        text=final_reply_text,
        reply_to_message_id=original_message_id,
        thread_id=thread_id,
    )

    # Send any detected generated files / images
    await send_detected_media(
        update=update,
        context=context,
        response_text=reply_text,
        thread_id=thread_id,
    )


# ---------------------------------------------------------------------------
# Group Mention Check
# ---------------------------------------------------------------------------

def is_group_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if a group message mentions the bot or replies to the bot."""
    msg = update.message
    if not msg:
        return False

    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE:
        return True

    if not REQUIRE_MENTION_IN_GROUPS:
        return True

    # Check if replying to the bot
    if msg.reply_to_message and msg.reply_to_message.from_user:
        if msg.reply_to_message.from_user.id == context.bot.id:
            return True

    # Check if bot is mentioned
    bot_username = (context.bot.username or "").lower()
    if bot_username:
        if f"@{bot_username}" in (msg.text or "").lower() or f"@{bot_username}" in (msg.caption or "").lower():
            return True

        if msg.entities:
            for ent in msg.entities:
                if ent.type == "mention":
                    mention_text = msg.text[ent.offset : ent.offset + ent.length].lower()
                    if mention_text == f"@{bot_username}":
                        return True
                elif ent.type == "bot_command":
                    cmd_text = msg.text[ent.offset : ent.offset + ent.length].lower()
                    if f"@{bot_username}" in cmd_text:
                        return True

    return False


def clean_mention(text: str, bot_username: Optional[str]) -> str:
    """Clean @bot_username from prompt text."""
    if not text or not bot_username:
        return text or ""
    return re.sub(rf"(?i)@{re.escape(bot_username)}\b", "", text).strip()


# ---------------------------------------------------------------------------
# Handlers: Text & Inbound Batching
# ---------------------------------------------------------------------------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text message with client-side split batching."""
    if not update.message or not update.message.text:
        return

    uid = update.effective_user.id
    if not is_authorized(uid):
        await update.message.reply_text(
            f"⛔ 未授權。你的 User ID: `{uid}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if not is_group_trigger(update, context):
        return

    raw_text = update.message.text
    bot_username = context.bot.username
    cleaned_text = clean_mention(raw_text, bot_username)

    if not cleaned_text:
        return

    # Buffer rapid text chunks for this user
    if uid in _pending_text_batches:
        _pending_text_batches[uid]["text"] += "\n" + cleaned_text
    else:
        _pending_text_batches[uid] = {
            "text": cleaned_text,
            "update": update,
            "message_id": update.message.message_id,
        }

    # Cancel previous flush timer
    prior_task = _pending_text_tasks.get(uid)
    if prior_task and not prior_task.done():
        prior_task.cancel()

    async def _flush_text():
        await asyncio.sleep(TEXT_BATCH_DELAY_SECONDS)
        batch = _pending_text_batches.pop(uid, None)
        _pending_text_tasks.pop(uid, None)
        if batch:
            await process_agent_turn(
                update=batch["update"],
                context=context,
                prompt=batch["text"],
                original_message_id=batch["message_id"],
            )

    _pending_text_tasks[uid] = asyncio.create_task(_flush_text())


# ---------------------------------------------------------------------------
# Handlers: Photos & Albums
# ---------------------------------------------------------------------------

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photos and photo albums with batching."""
    if not update.message or not update.message.photo:
        return

    uid = update.effective_user.id
    if not is_authorized(uid):
        return

    if not is_group_trigger(update, context):
        return

    # Download largest photo
    photo = update.message.photo[-1]
    saved_path = await save_photo(photo)

    caption = update.message.caption or ""
    caption = clean_mention(caption, context.bot.username)

    media_group_id = update.message.media_group_id
    batch_key = f"{uid}:{media_group_id}" if media_group_id else f"{uid}:single_photo"

    if batch_key in _pending_photo_batches:
        _pending_photo_batches[batch_key]["paths"].append(str(saved_path))
        if caption and not _pending_photo_batches[batch_key]["caption"]:
            _pending_photo_batches[batch_key]["caption"] = caption
    else:
        _pending_photo_batches[batch_key] = {
            "paths": [str(saved_path)],
            "caption": caption,
            "update": update,
            "message_id": update.message.message_id,
        }

    prior_task = _pending_photo_tasks.get(batch_key)
    if prior_task and not prior_task.done():
        prior_task.cancel()

    async def _flush_photo():
        await asyncio.sleep(MEDIA_BATCH_DELAY_SECONDS)
        batch = _pending_photo_batches.pop(batch_key, None)
        _pending_photo_tasks.pop(batch_key, None)
        if batch:
            paths = batch["paths"]
            cap = batch["caption"]
            if len(paths) == 1:
                prompt = f"[📎 使用者傳送了圖片: {paths[0]}]\n{cap}".strip()
            else:
                paths_str = "\n".join(f"- {p}" for p in paths)
                prompt = f"[📎 使用者傳送了相簿 ({len(paths)} 張圖片):\n{paths_str}]\n{cap}".strip()

            await process_agent_turn(
                update=batch["update"],
                context=context,
                prompt=prompt,
                original_message_id=batch["message_id"],
            )

    _pending_photo_tasks[batch_key] = asyncio.create_task(_flush_photo())


# ---------------------------------------------------------------------------
# Handlers: Voice & Audio
# ---------------------------------------------------------------------------

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming voice messages."""
    if not update.message or not update.message.voice:
        return

    uid = update.effective_user.id
    if not is_authorized(uid):
        return

    if not is_group_trigger(update, context):
        return

    saved_path = await save_voice(update.message.voice)
    prompt = f"[🎙️ 使用者傳送了語音音檔: {saved_path}]\n請聆聽/分析該語音內容並給予回覆。"

    await process_agent_turn(
        update=update,
        context=context,
        prompt=prompt,
        original_message_id=update.message.message_id,
    )


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming audio files."""
    if not update.message or not update.message.audio:
        return

    uid = update.effective_user.id
    if not is_authorized(uid):
        return

    if not is_group_trigger(update, context):
        return

    saved_path = await save_audio(update.message.audio)
    caption = clean_mention(update.message.caption or "", context.bot.username)
    prompt = f"[🎵 使用者傳送了音樂/音訊檔案: {saved_path} (名稱: {update.message.audio.file_name or 'audio'})]\n{caption}".strip()

    await process_agent_turn(
        update=update,
        context=context,
        prompt=prompt,
        original_message_id=update.message.message_id,
    )


# ---------------------------------------------------------------------------
# Handlers: Documents & Files
# ---------------------------------------------------------------------------

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming document or code files."""
    if not update.message or not update.message.document:
        return

    uid = update.effective_user.id
    if not is_authorized(uid):
        return

    if not is_group_trigger(update, context):
        return

    doc = update.message.document
    saved_path = await save_document(doc)
    caption = clean_mention(update.message.caption or "", context.bot.username)
    prompt = f"[📁 使用者傳送了文件檔案: {saved_path} (原始檔名: {doc.file_name or 'file'})]\n{caption}".strip()

    await process_agent_turn(
        update=update,
        context=context,
        prompt=prompt,
        original_message_id=update.message.message_id,
    )


# ---------------------------------------------------------------------------
# Handlers: Location
# ---------------------------------------------------------------------------

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle shared location pins."""
    if not update.message or not update.message.location:
        return

    uid = update.effective_user.id
    if not is_authorized(uid):
        return

    loc = update.message.location
    venue = update.message.venue
    venue_info = f"\n地點名稱: {venue.title}\n地址: {venue.address}" if venue else ""
    prompt = (
        f"[📍 使用者分享了地理座標]\n"
        f"緯度 (Latitude): {loc.latitude}\n"
        f"經度 (Longitude): {loc.longitude}"
        f"{venue_info}\n"
        f"地圖連結: https://www.google.com/maps/search/?api=1&query={loc.latitude},{loc.longitude}"
    )

    await process_agent_turn(
        update=update,
        context=context,
        prompt=prompt,
        original_message_id=update.message.message_id,
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    user = update.effective_user
    uid = user.id

    if not is_authorized(uid):
        await send_formatted_reply(
            update=update,
            context=context,
            text=f"⛔ 你沒有權限使用此 Bot。\n你的 User ID: `{uid}`",
            reply_to_message_id=update.message.message_id,
        )
        return

    current_model = get_user_model(uid)
    text = (
        f"👋 你好 **{user.first_name}**！\n\n"
        f"我是你的 **Antigravity AI Agent** 遠端助手。\n"
        f"直接傳送文字、圖片、語音或程式碼文件，我將為你處理。\n\n"
        f"🧠 **目前模型：** `{current_model}`\n"
        f"🆔 **你的 User ID：** `{uid}`\n\n"
        f"📌 **常用指令：**\n"
        f"• `/usage` — 📊 查看 Token 用量與資源消耗統計\n"
        f"• `/model` — 🧠 互動式切換 AI 模型\n"
        f"• `/compact` — 📦 壓縮上下文（瘦身並保留關鍵記憶）\n"
        f"• `/reset` — 🔄 重置會話記憶（開新對話）\n"
        f"• `/status` — 📈 查看目前運作狀態\n"
        f"• `/cancel` — 🛑 中止正在運行的任務\n"
        f"• `/clear` — 🧹 清理暫存多模態檔案\n"
        f"• `/help` — 📖 顯示完整說明"
    )
    await send_formatted_reply(
        update=update,
        context=context,
        text=text,
        reply_to_message_id=update.message.message_id,
    )


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /model command with interactive pagination."""
    uid = update.effective_user.id
    if not is_authorized(uid):
        return

    args = context.args
    current_model = get_user_model(uid)

    if args:
        raw_model = " ".join(args).strip()
        new_model = resolve_model_alias(raw_model)
        set_user_model(uid, new_model)
        await send_formatted_reply(
            update=update,
            context=context,
            text=f"✅ **模型已切換為：** `{new_model}`\n會話已自動重置為全新對話。",
            reply_to_message_id=update.message.message_id,
        )
        return

    # Show interactive picker
    kb, page, total = build_model_keyboard(current_model, page=0)
    await update.message.reply_text(
        f"🧠 AI 模型選擇器\n\n"
        f"目前使用模型: {current_model}\n"
        f"點擊下方按鈕可立即切換模型（切換後將自動開啟新會話）：",
        reply_markup=kb,
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /reset and /new commands."""
    uid = update.effective_user.id
    if not is_authorized(uid):
        return

    reset_user_conversation(uid)
    await send_formatted_reply(
        update=update,
        context=context,
        text="🔄 **對話記憶已重置**\n下次傳送訊息將會開啟全新的 Agent Session。",
        reply_to_message_id=update.message.message_id,
    )


async def cmd_compact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /compact and /summarize commands to compress session history."""
    uid = update.effective_user.id
    if not is_authorized(uid):
        return

    if is_user_task_running(uid):
        await send_formatted_reply(
            update=update,
            context=context,
            text="⚠️ 目前有任務正在運行中，請稍候或先發送 /cancel 中止任務後再進行壓縮。",
            reply_to_message_id=update.message.message_id,
        )
        return

    status_msg = await update.message.reply_text("📦 正在啟動上下文壓縮 (Context Compression)...")

    async def on_progress(text: str):
        try:
            await status_msg.edit_text(text)
        except Exception:
            pass

    start_time = time.time()
    success, result_text, old_tok, new_tok = await compact_user_conversation(
        user_id=uid,
        on_progress=on_progress,
    )
    elapsed = int(time.time() - start_time)

    if not success:
        try:
            await status_msg.edit_text(f"❌ 壓縮失敗：{result_text}")
        except Exception:
            pass
        return

    saved_pct = ((old_tok - new_tok) / max(old_tok, 1)) * 100 if old_tok > 0 else 0
    card = (
        f"✨ **會話上下文壓縮成功！**\n\n"
        f"📊 **Token 瘦身統計：**\n"
        f"• 壓縮前：`{old_tok:,}` tokens\n"
        f"• 壓縮後：`{new_tok:,}` tokens\n"
        f"• 空間釋放：`{saved_pct:.1f}%` (耗時 {elapsed}s)\n\n"
        f"📝 **提煉之核心記憶摘要：**\n\n"
        f"{result_text}\n\n"
        f"---\n"
        f"💡 *新會話已成功就緒，隨時傳送訊息繼續工作！*"
    )

    try:
        await status_msg.delete()
    except Exception:
        pass

    await send_formatted_reply(
        update=update,
        context=context,
        text=card,
        reply_to_message_id=update.message.message_id,
    )


async def cmd_usage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /usage command to display detailed token statistics."""
    user = update.effective_user
    uid = user.id
    if not is_authorized(uid):
        return

    current_model = get_user_model(uid)
    conv_id = get_user_conversation(uid)
    usage_summary = get_user_usage_summary(uid)

    card = format_usage_card(
        user_id=uid,
        user_name=user.first_name,
        current_model=current_model,
        conversation_id=conv_id,
        usage_summary=usage_summary,
    )
    await send_formatted_reply(
        update=update,
        context=context,
        text=card,
        reply_to_message_id=update.message.message_id,
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command."""
    user = update.effective_user
    uid = user.id
    if not is_authorized(uid):
        return

    current_model = get_user_model(uid)
    conv_id = get_user_conversation(uid)
    is_running = is_user_task_running(uid)
    uptime_str = get_uptime_string()
    usage_summary = get_user_usage_summary(uid)

    card = format_status_card(
        user_id=uid,
        user_name=user.first_name,
        current_model=current_model,
        conversation_id=conv_id,
        uptime_str=uptime_str,
        is_running=is_running,
        workspace_dir=WORKSPACE_DIR,
        proxy_url=PROXY_URL or None,
        usage_stats=usage_summary,
    )
    await send_formatted_reply(
        update=update,
        context=context,
        text=card,
        reply_to_message_id=update.message.message_id,
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cancel command."""
    uid = update.effective_user.id
    if not is_authorized(uid):
        return

    cancelled = await cancel_user_task(uid)
    if cancelled:
        await send_formatted_reply(
            update=update,
            context=context,
            text="🛑 **正在運行的 Agent 任務已成功中止。**",
            reply_to_message_id=update.message.message_id,
        )
    else:
        await send_formatted_reply(
            update=update,
            context=context,
            text="ℹ️ 目前沒有正在執行的任務。",
            reply_to_message_id=update.message.message_id,
        )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /clear command to clean up local media cache."""
    uid = update.effective_user.id
    if not is_authorized(uid):
        return

    count = cleanup_cache(max_age_seconds=0)  # clean all
    await send_formatted_reply(
        update=update,
        context=context,
        text=f"🧹 已清理 {count} 個暫存多模態檔案。",
        reply_to_message_id=update.message.message_id,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    uid = update.effective_user.id
    if not is_authorized(uid):
        return

    current_model = get_user_model(uid)
    card = format_help_card(current_model, AGY_TIMEOUT)
    await send_formatted_reply(
        update=update,
        context=context,
        text=card,
        reply_to_message_id=update.message.message_id,
    )


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /memory command to view, search, and manage persistent memory."""
    uid = update.effective_user.id
    if not is_authorized(uid):
        return

    from memory_manager import read_entries, add_entry, remove_entry, search_memories, MEMORY_DIR

    args = context.args or []
    if args:
        subcmd = args[0].lower()
        if subcmd == "search" and len(args) > 1:
            query = " ".join(args[1:])
            results = search_memories(query)
            if not results:
                await send_formatted_reply(update, context, f"🔍 未找到包含「{query}」的記憶。")
                return
            lines = [f"🔍 *記憶搜尋結果（關鍵字：`{query}`）：*\n"]
            for idx, r in enumerate(results, 1):
                lines.append(f"*{idx}. [{r['store']}]*\n{r['content']}\n")
            await send_formatted_reply(update, context, "\n".join(lines))
            return

        elif subcmd == "add" and len(args) > 1:
            target = "memory"
            text_idx = 1
            if args[1].lower() in ("user", "pref", "preference"):
                target = "user"
                text_idx = 2
            content = " ".join(args[text_idx:]).strip()
            if not content:
                await send_formatted_reply(update, context, "⚠️ 請輸入欲儲存的記憶內容。")
                return
            ok, msg = add_entry(target, content)
            await send_formatted_reply(update, context, msg)
            return

        elif subcmd in ("remove", "rm", "del", "delete") and len(args) > 1:
            target = "memory"
            text_idx = 1
            if args[1].lower() in ("user", "pref"):
                target = "user"
                text_idx = 2
            query = " ".join(args[text_idx:]).strip()
            ok, msg = remove_entry(target, query)
            await send_formatted_reply(update, context, msg)
            return

    # Default overview card
    user_entries = read_entries("user")
    sys_entries = read_entries("memory")

    overview = (
        "🧠 **本機持久化記憶管理 (Hermes Architecture)**\n"
        f"📂 儲存路徑：`{MEMORY_DIR}`\n\n"
        f"👤 **用戶偏好與規則 (USER.md)**：共 `{len(user_entries)}` 條\n"
        f"🖥️ **系統事實與環境配置 (MEMORY.md)**：共 `{len(sys_entries)}` 條\n\n"
        "💡 *提示：本機記憶永久保存，即使 Bot 重啟或掉線也不會丟失。每輪會話啟動時自動作為背景快照注入。*\n"
        "• 搜尋記憶：`/memory search <關鍵字>`\n"
        "• 添加記憶：`/memory add [user] <內容>`\n"
        "• 刪除記憶：`/memory remove [user] <關鍵字>`"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👤 查看 USER.md", callback_data="view_mem:user"),
            InlineKeyboardButton("🖥️ 查看 MEMORY.md", callback_data="view_mem:sys"),
        ],
        [InlineKeyboardButton("❌ 關閉", callback_data="view_mem:close")]
    ])

    await update.message.reply_text(
        text=overview,
        reply_markup=kb,
    )


# ---------------------------------------------------------------------------
# Callback Query Handler (Interactive Buttons)
# ---------------------------------------------------------------------------

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button clicks."""
    query = update.callback_query
    await query.answer()

    data = query.data
    uid = update.effective_user.id
    if not is_authorized(uid):
        return

    if data.startswith("set_model:"):
        model_name = data.split(":", 1)[1]
        set_user_model(uid, model_name)
        await query.edit_message_text(
            f"✅ 模型已成功切換為：{model_name}\n"
            f"對話記憶已重置，隨時傳送訊息即可開始新對話！",
        )

    elif data.startswith("page_model:"):
        page = int(data.split(":", 1)[1])
        current_model = get_user_model(uid)
        kb, page, total = build_model_keyboard(current_model, page=page)
        await query.edit_message_reply_markup(reply_markup=kb)

    elif data == "close_model_picker":
        await query.message.delete()

    elif data == "cancel_task":
        cancelled = await cancel_user_task(uid)
        if cancelled:
            await query.edit_message_text("🛑 任務已被使用者中止。")
        else:
            await query.answer("任務已結束或未在運行中", show_alert=True)

    elif data.startswith("view_mem:"):
        target = data.split(":", 1)[1]
        if target == "close":
            await query.message.delete()
            return

        from memory_manager import read_entries
        if target == "user":
            entries = read_entries("user")
            title = "👤 **用戶偏好與規則 (USER.md)**"
        else:
            entries = read_entries("memory")
            title = "🖥️ **系統事實與環境配置 (MEMORY.md)**"

        if not entries:
            text = f"{title}\n\n目前尚無記錄。"
        else:
            lines = [f"{title}\n"]
            for idx, e in enumerate(entries, 1):
                lines.append(f"**{idx}.** {e}\n")
            text = "\n".join(lines)

        if len(text) > 3800:
            text = text[:3700] + "\n\n...（其餘條目請於本地檔案查看）"

        await query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 返回總覽", callback_data="view_mem_overview")]
            ])
        )

    elif data == "view_mem_overview":
        from memory_manager import read_entries, MEMORY_DIR
        user_entries = read_entries("user")
        sys_entries = read_entries("memory")
        overview = (
            "🧠 **本機持久化記憶管理 (Hermes Architecture)**\n"
            f"📂 儲存路徑：`{MEMORY_DIR}`\n\n"
            f"👤 **用戶偏好與規則 (USER.md)**：共 `{len(user_entries)}` 條\n"
            f"🖥️ **系統事實與環境配置 (MEMORY.md)**：共 `{len(sys_entries)}` 條\n\n"
            "• 搜尋記憶：`/memory search <關鍵字>`\n"
            "• 添加記憶：`/memory add [user] <內容>`\n"
            "• 刪除記憶：`/memory remove [user] <關鍵字>`"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("👤 查看 USER.md", callback_data="view_mem:user"),
                InlineKeyboardButton("🖥️ 查看 MEMORY.md", callback_data="view_mem:sys"),
            ],
            [InlineKeyboardButton("❌ 關閉", callback_data="view_mem:close")]
        ])
        await query.edit_message_text(text=overview, reply_markup=kb)

    elif data == "noop":
        pass


# ---------------------------------------------------------------------------
# Network Resilience & Polling Error Recovery (Hermes Style)
# ---------------------------------------------------------------------------

async def drain_polling_connections(app: Application) -> None:
    """Reset the httpx connection pool used for getUpdates polling."""
    if not (app and app.bot):
        return
    try:
        polling_req = app.bot._request[0]  # noqa: SLF001
        await polling_req.shutdown()
        await polling_req.initialize()
        logger.debug("Polling connection pool drained successfully")
    except Exception as e:
        logger.debug("Failed draining polling pool: %s", e)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log uncaught errors gracefully."""
    logger.error("Exception while handling an update: %s", context.error, exc_info=context.error)


async def post_init(app: Application) -> None:
    """Register slash commands with Telegram to enable auto-completion."""
    commands = [
        BotCommand("usage", "📊 查看 Token 用量與資源消耗統計"),
        BotCommand("model", "🧠 切換 AI 模型"),
        BotCommand("memory", "🧠 查看與管理本機持久記憶 (MEMORY.md / USER.md)"),
        BotCommand("compact", "📦 壓縮當前會話上下文（瘦身並保留關鍵記憶）"),
        BotCommand("status", "📈 查看系統狀態與當前會話"),
        BotCommand("reset", "🔄 重置會話記憶（開啟新對話）"),
        BotCommand("cancel", "🛑 中止正在運行的任務"),
        BotCommand("clear", "🧹 清理暫存多模態檔案"),
        BotCommand("help", "📖 顯示說明手冊"),
    ]
    try:
        await app.bot.set_my_commands(commands)
        logger.info("Telegram bot commands registered successfully")
    except Exception as e:
        logger.warning("Failed to register bot commands: %s", e)


# ---------------------------------------------------------------------------
# Main Application Builder
# ---------------------------------------------------------------------------

def main() -> None:
    """Launch the Telegram Bot with network resilience and full handler pipeline."""
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your-bot-token-here":
        print("❌ 請在 .env 檔案中設定 TELEGRAM_BOT_TOKEN")
        sys.exit(1)

    if not ALLOWED_USER_IDS:
        logger.warning("⚠️ ALLOWED_USER_IDS 未設定 — 所有人皆能使用此 Bot！")

    builder = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init)

    # Proxy Support
    if PROXY_URL:
        builder = builder.proxy(PROXY_URL).get_updates_proxy(PROXY_URL)
        logger.info("Using Proxy: %s", PROXY_URL)

    app = builder.build()

    # Global error handler
    app.add_error_handler(error_handler)

    # Commands
    app.add_handler(CommandHandler(["start"], cmd_start))
    app.add_handler(CommandHandler(["usage"], cmd_usage))
    app.add_handler(CommandHandler(["model", "models"], cmd_model))
    app.add_handler(CommandHandler(["memory", "mem"], cmd_memory))
    app.add_handler(CommandHandler(["compact", "summarize"], cmd_compact))
    app.add_handler(CommandHandler(["reset", "new"], cmd_reset))
    app.add_handler(CommandHandler(["status"], cmd_status))
    app.add_handler(CommandHandler(["cancel", "stop"], cmd_cancel))
    app.add_handler(CommandHandler(["clear"], cmd_clear))
    app.add_handler(CommandHandler(["help"], cmd_help))

    # Inline Keyboard Callbacks
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    # Media & Message Handlers
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🚀 Antigravity Telegram Bot 正在啟動...")
    logger.info("📂 工作目錄: %s", WORKSPACE_DIR)
    logger.info("🧠 預設模型: %s", DEFAULT_MODEL)
    logger.info("👥 授權使用者: %s", ALLOWED_USER_IDS or "(所有人)")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
