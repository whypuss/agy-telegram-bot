"""
Antigravity CLI (agy) Execution & Process Management Engine.

Features:
- Executes agy CLI turns with conversation persistence (--conversation)
- Asynchronous stderr streaming for real-time progress parsing
- Task cancellation support (terminating and killing running child processes)
- Robust error extraction and timeout handling
"""

import asyncio
import json
import logging
import os
import re
import signal
import time
from typing import Callable, Coroutine, Dict, Optional, Tuple

from config import (
    AGY_PATH,
    AGY_TIMEOUT,
    DEFAULT_MODEL,
    WORKSPACE_DIR,
    AGENT_SYSTEM_PROMPT,
)

logger = logging.getLogger("agy-tg-bot.runner")

# User ID -> Active subprocess
_active_processes: Dict[int, asyncio.subprocess.Process] = {}

# User ID -> Active Conversation ID
user_conversations: Dict[int, str] = {}

# User ID -> Selected Model
user_models: Dict[int, str] = {}

# User ID -> Active Session Usage dict (cumulative for active conversation)
user_session_usage: Dict[int, dict] = {}

# User ID -> Last Turn Usage dict (delta for the latest single turn)
user_last_turn_usage: Dict[int, dict] = {}

# User ID -> Lifetime Usage dict (total across all sessions since bot startup)
user_lifetime_usage: Dict[int, dict] = {}


def get_user_model(user_id: int) -> str:
    """Get the current model for a user, or default."""
    return user_models.get(user_id, DEFAULT_MODEL)


def set_user_model(user_id: int, model_name: str) -> None:
    """Set the model for a user and reset their conversation context."""
    user_models[user_id] = model_name
    reset_user_conversation(user_id)


def get_user_conversation(user_id: int) -> Optional[str]:
    """Get the active conversation ID for a user."""
    return user_conversations.get(user_id)


def reset_user_conversation(user_id: int) -> None:
    """Reset the conversation context and session usage for a user."""
    user_conversations.pop(user_id, None)
    user_session_usage.pop(user_id, None)
    user_last_turn_usage.pop(user_id, None)


def get_user_usage_summary(user_id: int) -> dict:
    """Get usage summary including current session, last turn, and lifetime statistics."""
    return {
        "session": user_session_usage.get(user_id),
        "last_turn": user_last_turn_usage.get(user_id),
        "lifetime": user_lifetime_usage.get(
            user_id,
            {"turns": 0, "total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0},
        ),
    }


def is_user_task_running(user_id: int) -> bool:
    """Check if a task is currently executing for a user."""
    proc = _active_processes.get(user_id)
    return proc is not None and proc.returncode is None


async def cancel_user_task(user_id: int) -> bool:
    """Cancel and terminate any currently running task for the specified user."""
    proc = _active_processes.get(user_id)
    if not proc or proc.returncode is not None:
        return False

    logger.info("Cancelling active agy task for user %s (PID %s)", user_id, proc.pid)
    try:
        proc.terminate()
        # Wait up to 2 seconds for graceful exit, then force kill
        for _ in range(20):
            if proc.returncode is not None:
                break
            await asyncio.sleep(0.1)
        if proc.returncode is None:
            proc.kill()
    except ProcessLookupError:
        pass
    except Exception as e:
        logger.warning("Error terminating process for user %s: %s", user_id, e)
    finally:
        _active_processes.pop(user_id, None)
    return True


# ---------------------------------------------------------------------------
# Progress Parser Helper
# ---------------------------------------------------------------------------

def _parse_step_update_to_indicator(su: dict) -> Optional[str]:
    """Parse an agy stream-json step_update event into a user-friendly indicator string."""
    if not isinstance(su, dict):
        return None

    step_type = su.get("step_type")
    state = su.get("state")  # "ACTIVE" or "DONE"
    dur = su.get("duration_seconds")

    if step_type == "tool":
        tool_name = su.get("tool_name") or ""
        tool_info = su.get("tool_info") or {}
        params = tool_info.get("parameters") or {}

        # Tool specific formatting
        if tool_name == "run_command":
            cmd = params.get("CommandLine", "").strip()
            if len(cmd) > 45:
                cmd = cmd[:42] + "..."
            desc = f"執行終端指令 `{cmd}`" if cmd else "執行終端指令"
        elif tool_name == "view_file":
            path = os.path.basename(params.get("AbsolutePath", "")) or params.get("AbsolutePath", "")
            desc = f"讀取檔案 `{path}`" if path else "讀取檔案"
        elif tool_name in ("replace_file_content", "sed_file", "multi_replace_file_content"):
            path = os.path.basename(params.get("TargetFile", "")) or params.get("TargetFile", "")
            desc = f"編輯修改檔案 `{path}`" if path else "編輯修改檔案"
        elif tool_name == "write_to_file":
            path = os.path.basename(params.get("TargetFile", "")) or params.get("TargetFile", "")
            desc = f"寫入/建立檔案 `{path}`" if path else "寫入/建立檔案"
        elif tool_name == "list_dir":
            path = os.path.basename(params.get("DirectoryPath", "")) or params.get("DirectoryPath", "")
            desc = f"瀏覽目錄清單 `{path}`" if path else "瀏覽目錄清單"
        elif tool_name in ("grep_search", "find_by_name"):
            query = params.get("Query") or params.get("Pattern") or ""
            if len(query) > 35:
                query = query[:32] + "..."
            desc = f"檢索代碼/檔案 `{query}`" if query else "檢索代碼/檔案"
        elif tool_name in ("search_web", "read_url_content"):
            q = params.get("query") or params.get("Url") or ""
            if len(q) > 35:
                q = q[:32] + "..."
            desc = f"聯網搜尋檢索 `{q}`" if q else "聯網搜尋檢索"
        elif tool_name in ("invoke_subagent", "define_subagent"):
            desc = "調度多代理子任務 (Subagent)"
        elif tool_name == "schedule":
            desc = "排程任務設定"
        elif tool_name == "generate_image":
            desc = "生成繪製圖片"
        elif tool_name.startswith("browser_"):
            desc = f"調用無頭瀏覽器 ({tool_name})"
        else:
            desc = f"調用工具 `{tool_name}`"

        if state == "ACTIVE":
            return f"▶ 🔧 {desc}"
        elif state == "DONE":
            dur_str = f" ({dur:.1f}s)" if (dur is not None and dur > 0.05) else ""
            return f"✓ 🔧 {desc}{dur_str}"

    elif step_type == "agent_response":
        if state == "ACTIVE":
            if su.get("text_delta"):
                return "▶ 📝 正在生成回答..."
            else:
                return "▶ 🧠 深度思考分析與步驟規劃..."
        elif state == "DONE":
            dur = su.get("duration_seconds")
            dur_str = f" ({dur:.1f}s)" if (dur is not None and dur > 0.05) else ""
            if su.get("text_delta"):
                return f"✓ 📝 回答生成完畢{dur_str}"
            else:
                return f"✓ 🧠 思考規劃完成{dur_str}"

    return None


def _parse_stderr_line_to_indicator(line: str) -> Optional[str]:
    """Parse a single stderr line from agy into a user-friendly status indicator (fallback)."""
    if not line or not line.strip():
        return None
    
    # Skip noisy HTTP logging
    if "httpx" in line or ("202" in line and "POST" in line):
        return None

    line_clean = line.strip()
    line_lower = line_clean.lower()

    if "tool" in line_lower or "executing" in line_lower or "running" in line_lower:
        return f"🔧 {line_clean[:90]}"
    elif "search" in line_lower:
        return f"🔍 {line_clean[:90]}"
    elif "file" in line_lower or "read" in line_lower or "writ" in line_lower or "edit" in line_lower:
        return f"📄 {line_clean[:90]}"
    elif "think" in line_lower or "reason" in line_lower or "plan" in line_lower:
        return f"🧠 {line_clean[:90]}"
    elif "error" in line_lower or "warn" in line_lower or "fail" in line_lower:
        return f"⚠️ {line_clean[:90]}"
    elif len(line_clean) > 0 and not line_clean.startswith("{"):
        return f"▸ {line_clean[:90]}"

    return None


# ---------------------------------------------------------------------------
# Core Execution Engine
# ---------------------------------------------------------------------------

async def run_agent_turn(
    prompt: str,
    user_id: int,
    on_progress: Optional[Callable[[str], Coroutine]] = None,
) -> Tuple[str, Optional[str], Optional[dict]]:
    """
    Execute a turn of the Antigravity Agent for a user.
    
    Args:
        prompt: User input prompt (may include media references)
        user_id: Telegram user ID
        on_progress: Async callback invoked with progress indicator strings
        
    Returns:
        (response_text, new_conversation_id, turn_usage)
    """
    model = get_user_model(user_id)
    conv_id = get_user_conversation(user_id)

    cmd = [AGY_PATH]
    cmd.extend(["--print-timeout", f"{AGY_TIMEOUT}s"])
    cmd.extend(["--dangerously-skip-permissions"])
    cmd.extend(["--output-format", "stream-json"])

    if model:
        cmd.extend(["--model", model])

    if conv_id:
        cmd.extend(["--conversation", conv_id])

    if WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
        cmd.extend(["--add-dir", WORKSPACE_DIR])

    # Append prompt to --print
    cmd.append(f"--print={prompt}")

    logger.info("Executing agy for user %s: %s", user_id, " ".join(cmd[:6]))

    # Set up child process environment
    env = {
        **os.environ,
        "PAGER": "cat",
        "PYTHONUNBUFFERED": "1",
    }

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=WORKSPACE_DIR if os.path.isdir(WORKSPACE_DIR) else None,
        env=env,
    )

    _active_processes[user_id] = proc
    stderr_lines: list[str] = []
    stdout_raw_lines: list[str] = []
    result_data: dict = {}

    # Close stdin since prompt is passed via CLI flag
    if proc.stdin:
        try:
            proc.stdin.close()
        except Exception:
            pass

    async def _stream_stdout():
        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break
            line_str = line_bytes.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue
            stdout_raw_lines.append(line_str)
            try:
                event_obj = json.loads(line_str)
            except Exception:
                continue

            if isinstance(event_obj, dict):
                event_type = event_obj.get("event")
                if event_type == "step_update":
                    su = event_obj.get("step_update", {})
                    indicator = _parse_step_update_to_indicator(su)
                    if indicator and on_progress:
                        try:
                            await on_progress(indicator)
                        except Exception:
                            pass
                elif event_type == "result":
                    res = event_obj.get("result", {})
                    if isinstance(res, dict):
                        result_data.update(res)

    async def _stream_stderr():
        while True:
            line_bytes = await proc.stderr.readline()
            if not line_bytes:
                break
            decoded = line_bytes.decode("utf-8", errors="replace").rstrip()
            if decoded:
                stderr_lines.append(decoded)
            
            indicator = _parse_stderr_line_to_indicator(decoded)
            if indicator and on_progress and not result_data:
                try:
                    await on_progress(indicator)
                except Exception:
                    pass

    stderr_task = asyncio.create_task(_stream_stderr())
    stdout_task = asyncio.create_task(_stream_stdout())

    try:
        await asyncio.wait_for(
            asyncio.gather(stderr_task, stdout_task, proc.wait()),
            timeout=AGY_TIMEOUT + 15,
        )
    except asyncio.CancelledError:
        logger.info("Task cancelled for user %s", user_id)
        await cancel_user_task(user_id)
        raise
    finally:
        if not stderr_task.done():
            stderr_task.cancel()
        if not stdout_task.done():
            stdout_task.cancel()
        _active_processes.pop(user_id, None)

    stderr_text = "\n".join(stderr_lines)

    response_text = ""
    new_conv_id = None
    turn_usage: Optional[dict] = None

    if result_data:
        response_text = result_data.get("response", "").strip()
        new_conv_id = result_data.get("conversation_id")
        num_turns = result_data.get("num_turns", 1)
        duration = result_data.get("duration_seconds", 0.0)
        raw_usage = result_data.get("usage")
    else:
        # Fallback to parsing raw stdout if result event was missing
        stdout_str = "\n".join(stdout_raw_lines).strip()
        try:
            parsed_json = json.loads(stdout_str)
            if isinstance(parsed_json, dict):
                response_text = parsed_json.get("response", "").strip()
                new_conv_id = parsed_json.get("conversation_id")
                num_turns = parsed_json.get("num_turns", 1)
                duration = parsed_json.get("duration_seconds", 0.0)
                raw_usage = parsed_json.get("usage")
                result_data = parsed_json
            else:
                response_text = stdout_str
                raw_usage = None
        except Exception:
            response_text = stdout_str
            raw_usage = None

    if isinstance(raw_usage, dict):
        input_tokens = raw_usage.get("input_tokens", 0)
        output_tokens = raw_usage.get("output_tokens", 0)
        thinking_tokens = raw_usage.get("thinking_tokens", 0)
        cache_read_tokens = raw_usage.get("cache_read_tokens", 0)
        total_tokens = raw_usage.get("total_tokens", 0)

        # Compute delta for this single turn against previous session total
        prev_session = user_session_usage.get(user_id, {})
        prev_total = prev_session.get("total_tokens", 0)
        prev_input = prev_session.get("input_tokens", 0)
        prev_output = prev_session.get("output_tokens", 0)
        prev_thinking = prev_session.get("thinking_tokens", 0)

        turn_input = max(0, input_tokens - prev_input) if prev_total > 0 else input_tokens
        turn_output = max(0, output_tokens - prev_output) if prev_total > 0 else output_tokens
        turn_thinking = max(0, thinking_tokens - prev_thinking) if prev_total > 0 else thinking_tokens
        turn_total = max(0, total_tokens - prev_total) if prev_total > 0 else total_tokens

        turn_usage = {
            "input_tokens": turn_input,
            "output_tokens": turn_output,
            "thinking_tokens": turn_thinking,
            "cache_read_tokens": cache_read_tokens,
            "total_tokens": turn_total,
            "duration_seconds": duration,
        }
        user_last_turn_usage[user_id] = turn_usage

        # Update cumulative session usage
        user_session_usage[user_id] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "thinking_tokens": thinking_tokens,
            "cache_read_tokens": cache_read_tokens,
            "total_tokens": total_tokens,
            "num_turns": num_turns,
        }

        # Update lifetime usage
        lifetime = user_lifetime_usage.setdefault(
            user_id,
            {"turns": 0, "total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0},
        )
        lifetime["turns"] += 1
        lifetime["total_tokens"] += turn_total
        lifetime["input_tokens"] += turn_input
        lifetime["output_tokens"] += turn_output
        lifetime["thinking_tokens"] += turn_thinking

    # Extract Conversation UUID from stderr logs if not present from JSON
    if not new_conv_id:
        for line in stderr_lines:
            if "conversation" in line.lower() or "session" in line.lower():
                match = re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', line)
                if match:
                    new_conv_id = match.group(0)
                    break

    if new_conv_id:
        user_conversations[user_id] = new_conv_id

    # Handle error cases
    if not response_text:
        if proc.returncode != 0 or result_data.get("status") == "ERROR":
            err_field = result_data.get("error") or result_data.get("message") or result_data.get("detail") or ""
            err_detail = (err_field or stderr_text or "").strip()
            
            logger.warning("agy exited with code %s. err_detail: %s", proc.returncode, err_detail[:500])
            
            err_lines = [f"❌ **Agent 執行失敗 (Exit Code: {proc.returncode})**"]
            
            # Check for timeout vs context saturation
            sess = user_session_usage.get(user_id, {})
            tot_tok = sess.get("total_tokens", 0)
            in_tok = sess.get("input_tokens", 0)
            turns = sess.get("num_turns", 0)
            
            if "timeout" in err_detail.lower():
                err_lines.append(
                    f"\n⏰ **執行超時 (Timeout: {AGY_TIMEOUT}s)**\n"
                    f"• 任務執行耗時達到上限（{AGY_TIMEOUT // 60} 分鐘），進程已被中止。\n"
                    f"• 期間累計消耗：`{tot_tok:,}` tokens (輸入 `{in_tok:,}` / 共 `{turns}` 輪)\n\n"
                    f"💡 **建議解法：**\n"
                    f"1. 如任務較長可繼續在 .env 調大 AGY_TIMEOUT\n"
                    f"2. 拆分步驟發送，或使用 `/compact` / `/reset` 減少每次步驟的上下文負擔"
                )
            elif tot_tok > 150000 or in_tok > 150000 or turns > 40:
                err_lines.append(
                    f"\n⚠️ **可能原因：會話上下文過載 (Context Saturation)**\n"
                    f"• 當前會話累積：`{tot_tok:,}` tokens (輸入 `{in_tok:,}` / 共 `{turns}` 輪)\n"
                    f"• 當歷史對話與終端輸出累積過長時，容易觸發模型上下文上限、單次步驟預算超限或語法解析異常。\n\n"
                    f"💡 **建議解法：**\n"
                    f"1. 輸入 `/compact` — 自動將歷史上下文提煉壓縮並無縫開啟新會話（保留進度與決策）\n"
                    f"2. 輸入 `/reset` — 直接重置會話記憶開啟全新對話"
                )
            
            if err_detail:
                err_lines.append(f"\n🔍 **錯誤詳情：**\n```\n{err_detail[:800]}\n```")
            elif tot_tok <= 150000:
                err_lines.append(
                    "\n⚠️ 核心進程未返回詳細標準錯誤輸出，可能因後端 API 請求中斷或終端指令解析異常。\n"
                    "💡 提示：可嘗試使用 `/reset` 開啟新對話，或發送簡短指令重試。"
                )
                
            response_text = "\n".join(err_lines)
        else:
            response_text = "（Agent 回覆為空）"

    return response_text, new_conv_id, turn_usage


async def compact_user_conversation(
    user_id: int,
    on_progress: Optional[Callable[[str], Coroutine]] = None,
) -> Tuple[bool, str, int, int]:
    """
    Compress active conversation context into a structured summary
    and seamlessly seed a fresh conversation session.
    
    Returns:
        (success, message/summary, old_total_tokens, new_total_tokens)
    """
    conv_id = get_user_conversation(user_id)
    if not conv_id:
        return False, "目前尚未建立任何會話記憶，無需壓縮。", 0, 0
    
    old_session = user_session_usage.get(user_id, {})
    old_tokens = old_session.get("total_tokens", 0)
    
    if on_progress:
        await on_progress("🧠 正在分析與提煉當前會話核心記憶...")
        
    # 1. Ask current conversation to generate structured summary
    summary_prompt = (
        "【系統指令：請為當前會話生成結構化壓縮記憶 (Context Summary)】\n"
        "請對本會話的所有歷史交互、已完成的任務與代碼變更、關鍵架構與技術決策、當前系統運行狀態，以及後續待辦事項，進行高密度、條理清晰的繁體中文總結。\n"
        "請按以下結構輸出：\n"
        "### 📋 項目與對話背景\n"
        "### ✅ 已完成事項與關鍵決策\n"
        "### 🔍 當前系統/服務/檔案狀態\n"
        "### 🎯 後續待辦與下一步規劃\n"
        "請直接輸出總結內容，勿帶客套話。"
    )
    
    summary_reply, _, _ = await run_agent_turn(
        prompt=summary_prompt,
        user_id=user_id,
        on_progress=on_progress,
    )
    
    if not summary_reply or summary_reply.startswith("❌"):
        return False, f"提煉對話記憶失敗：\n{summary_reply}", old_tokens, 0
    
    # 2. Reset conversation ID to open fresh session
    reset_user_conversation(user_id)
    
    if on_progress:
        await on_progress("🔄 正在注入壓縮記憶至全新會話...")
        
    # 3. Seed new session with the compact summary
    seed_prompt = (
        "【前續會話壓縮記憶注入】\n"
        "以下是上一會話壓縮後的項目狀態與決策摘要，請作為新會話的背景知識承接後續工作：\n\n"
        f"{summary_reply}\n\n"
        "請確認已載入該上下文，並簡短回覆「✅ 已成功載入前續記憶與項目狀態，請指示下一步工作。」"
    )
    
    seed_reply, new_conv_id, new_turn_usage = await run_agent_turn(
        prompt=seed_prompt,
        user_id=user_id,
        on_progress=on_progress,
    )
    
    new_session = user_session_usage.get(user_id, {})
    new_tokens = new_session.get("total_tokens", 0)
    
    return True, summary_reply, old_tokens, new_tokens

