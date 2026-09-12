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
from typing import Callable, Coroutine, Dict, List, Optional, Tuple

from config import (
    AGY_PATH,
    AGY_TIMEOUT,
    AGY_ADD_WORKSPACE_DIR,
    DEFAULT_MODEL,
    WORKSPACE_DIR,
    AGENT_SYSTEM_PROMPT,
    OPENCODE_DEFAULT_MODEL,
    is_opencode_model,
)

logger = logging.getLogger("agy-tg-bot.runner")

# Per-line ceiling for subprocess NDJSON streams. Well above any single event a
# turn produces, while still bounding memory if a backend goes haywire.
STREAM_LINE_LIMIT = 32 * 1024 * 1024

from session_store import (
    user_conversations,
    user_models,
    user_session_usage,
    user_last_turn_usage,
    user_lifetime_usage,
    get_user_model,
    set_user_model,
    get_user_conversation,
    set_user_conversation,
    reset_user_conversation,
    get_user_usage_summary,
    append_transcript,
    build_handoff_context,
    get_user_oc_session,
    reset_user_oc_session,
    clear_transcript,
    save_state,
)
from memory_manager import build_memory_context, monitor_and_extract, add_entry, add_session_summary

# User ID -> Active subprocess
_active_processes: Dict[int, asyncio.subprocess.Process] = {}

# Users whose running process was deliberately terminated via _cancel_agy_task
# (user cancel / correction steer). Used to convert the resulting non-zero
# exit into asyncio.CancelledError instead of a spurious "執行失敗" message.
_cancelled_users: set = set()

# User ID -> Last known agy cumulative usage (agy-only baseline for turn deltas,
# so interleaved OpenCode fallback turns never corrupt agy delta math)
_agy_last_cumulative: Dict[int, dict] = {}

# User ID -> Backend that served the last turn ("agy" | "opencode" | "agy+fallback")
_last_backend: Dict[int, str] = {}


def get_last_backend(user_id: int) -> str:
    """Return which backend served the user's last turn."""
    return _last_backend.get(user_id, "opencode" if is_opencode_model(get_user_model(user_id)) else "agy")


def is_user_task_running(user_id: int) -> bool:
    """Check if a task is currently executing for a user."""
    proc = _active_processes.get(user_id)
    if proc is not None and proc.returncode is None:
        return True
    try:
        from opencode_runner import is_oc_task_running
        return is_oc_task_running(user_id)
    except Exception:
        return False


async def cancel_user_task(user_id: int) -> bool:
    """Cancel and terminate any currently running task for the specified user."""
    cancelled = await _cancel_agy_task(user_id)
    try:
        from opencode_runner import cancel_oc_task
        if await cancel_oc_task(user_id):
            cancelled = True
    except Exception:
        pass
    return cancelled


async def _cancel_agy_task(user_id: int) -> bool:
    """Cancel and terminate any currently running agy task for the specified user."""
    proc = _active_processes.get(user_id)
    if not proc or proc.returncode is not None:
        return False

    logger.info("Cancelling active agy task for user %s (PID %s)", user_id, proc.pid)
    _cancelled_users.add(user_id)
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

async def _run_agy_turn(
    prompt: str,
    user_id: int,
    on_progress: Optional[Callable[[str], Coroutine]] = None,
) -> Tuple[str, Optional[str], Optional[dict]]:
    """
    Execute a turn of the Antigravity Agent for a user (agy backend only).

    Returns:
        (response_text, new_conversation_id, turn_usage)
    """
    model = get_user_model(user_id)
    # Guard: never pass an OpenCode model id to the agy CLI (can happen when
    # compacting an agy session while the user's active model is OpenCode).
    if is_opencode_model(model):
        model = DEFAULT_MODEL
    conv_id = get_user_conversation(user_id)

    # Hermes Frozen Snapshot Pattern:
    # If starting a fresh session (no conv_id), inject persistent memory context (USER.md + MEMORY.md)
    effective_prompt = prompt
    if not conv_id:
        mem_ctx = build_memory_context()
        if mem_ctx:
            effective_prompt = f"{mem_ctx}\n{prompt}"

        # agy only reads AGENTS.md when --add-dir is passed, and that flag also
        # drags in every skill description (+4,808 tokens). Carry standing
        # instructions here instead — first turn only, so the cached prefix
        # stays byte-identical across the rest of the conversation.
        if AGENT_SYSTEM_PROMPT:
            effective_prompt = f"[系統指示: {AGENT_SYSTEM_PROMPT}]\n{effective_prompt}"

    # Cross-backend handoff: if the user recently chatted on the other backend
    # (e.g. switched model after quota exhaustion), inject those missed turns
    # so this backend continues seamlessly instead of losing memory.
    handoff_ctx = build_handoff_context(user_id, "agy")
    if handoff_ctx:
        effective_prompt = f"{handoff_ctx}\n\n{effective_prompt}"

    cmd = [AGY_PATH]
    cmd.extend(["--print-timeout", f"{AGY_TIMEOUT}s"])
    cmd.extend(["--dangerously-skip-permissions"])
    cmd.extend(["--output-format", "stream-json"])

    if model:
        cmd.extend(["--model", model])

    if conv_id:
        cmd.extend(["--conversation", conv_id])

    if AGY_ADD_WORKSPACE_DIR and WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
        cmd.extend(["--add-dir", WORKSPACE_DIR])

    # Append prompt to --print
    cmd.append(f"--print={effective_prompt}")

    logger.info("Executing agy for user %s: %s", user_id, " ".join(cmd[:6]))

    # Set up child process environment
    env = {
        **os.environ,
        "PAGER": "cat",
        "PYTHONUNBUFFERED": "1",
    }

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=WORKSPACE_DIR if os.path.isdir(WORKSPACE_DIR) else None,
        env=env,
        # asyncio's default StreamReader limit is 64 KiB. agy emits one NDJSON
        # object per line, and a single line — the result event of a long turn,
        # or a large tool observation — routinely exceeds that. readline() then
        # raises, the stream is abandoned mid-flight, the terminating `result`
        # event never arrives, and the caller falls back to echoing raw stdout.
        # That is how /compact once returned the event stream as its "summary".
        limit=STREAM_LINE_LIMIT,
    )

    _active_processes[user_id] = proc
    _cancelled_users.discard(user_id)
    stderr_lines: list[str] = []
    stdout_raw_lines: list[str] = []
    result_data: dict = {}
    result_event = asyncio.Event()

    async def _stream_stdout():
        while True:
            try:
                line_bytes = await proc.stdout.readline()
            except Exception as e:
                # Never swallow this. Abandoning the stream here means the
                # `result` event is lost and the turn degrades into a raw dump,
                # which looks like a model failure rather than a read failure.
                logger.error("stdout stream aborted for user %s: %s: %s",
                             user_id, type(e).__name__, e)
                break
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
                    result_event.set()

    async def _stream_stderr():
        while True:
            try:
                line_bytes = await proc.stderr.readline()
            except Exception:
                break
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

    async def _wait_process_completion():
        proc_wait_task = asyncio.create_task(proc.wait())
        result_wait_task = asyncio.create_task(result_event.wait())
        done, pending = await asyncio.wait(
            [proc_wait_task, result_wait_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()

        # If result was already received, give process 2s to cleanly exit
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=1.5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

        # Give streams up to 1.5s to flush remaining buffers
        try:
            await asyncio.wait_for(asyncio.gather(stderr_task, stdout_task), timeout=1.5)
        except asyncio.TimeoutError:
            pass

    try:
        await asyncio.wait_for(
            _wait_process_completion(),
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
        if proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
        _active_processes.pop(user_id, None)

    # Process was deliberately terminated (user cancel / correction steer):
    # surface as a cancellation so the bot layer can stay silent or re-merge,
    # instead of showing a spurious "Exit Code: -15" failure.
    if user_id in _cancelled_users:
        _cancelled_users.discard(user_id)
        logger.info("agy task for user %s terminated by cancellation", user_id)
        raise asyncio.CancelledError()

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
        # Fallback when the result event was missing. stdout is NDJSON, so
        # json.loads() over the whole buffer always fails once there is more
        # than one line — which used to drop straight through to echoing the
        # raw event stream at the user. Recover the result line by line first,
        # and if there is genuinely no result, salvage the streamed text_delta
        # fragments rather than dumping protocol noise.
        stdout_str = "\n".join(stdout_raw_lines).strip()
        recovered, deltas = None, []
        for line in stdout_raw_lines:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("event") == "result" and isinstance(obj.get("result"), dict):
                recovered = obj["result"]
            elif obj.get("event") == "step_update":
                frag = (obj.get("step_update") or {}).get("text_delta")
                if frag:
                    deltas.append(frag)

        if recovered:
            logger.warning("Recovered result event from raw stdout for user %s", user_id)
            stdout_str = json.dumps(recovered)
        elif deltas:
            logger.warning("No result event for user %s; reassembled %d streamed fragments",
                           user_id, len(deltas))
            stdout_str = json.dumps({"response": "".join(deltas)})

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

        # Compute delta for this single turn against the agy-only baseline.
        # (Session totals are updated additively so interleaved OpenCode
        # fallback turns never corrupt agy delta math.)
        baseline = _agy_last_cumulative.get(user_id)
        if baseline is None:
            sess = user_session_usage.get(user_id, {})
            if sess.get("total_tokens"):
                baseline = dict(sess)
            else:
                baseline = {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0}
        prev_total = baseline.get("total_tokens", 0)
        prev_input = baseline.get("input_tokens", 0)
        prev_output = baseline.get("output_tokens", 0)
        prev_thinking = baseline.get("thinking_tokens", 0)

        turn_input = max(0, input_tokens - prev_input) if prev_total > 0 else input_tokens
        turn_output = max(0, output_tokens - prev_output) if prev_total > 0 else output_tokens
        turn_thinking = max(0, thinking_tokens - prev_thinking) if prev_total > 0 else thinking_tokens
        turn_total = max(0, total_tokens - prev_total) if prev_total > 0 else total_tokens
        _agy_last_cumulative[user_id] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "thinking_tokens": thinking_tokens,
            "total_tokens": total_tokens,
        }

        turn_usage = {
            "input_tokens": turn_input,
            "output_tokens": turn_output,
            "thinking_tokens": turn_thinking,
            "cache_read_tokens": cache_read_tokens,
            "total_tokens": turn_total,
            "duration_seconds": duration,
        }
        user_last_turn_usage[user_id] = turn_usage

        # Update cumulative session usage additively
        prev_session = user_session_usage.get(user_id, {})
        user_session_usage[user_id] = {
            "input_tokens": prev_session.get("input_tokens", 0) + turn_input,
            "output_tokens": prev_session.get("output_tokens", 0) + turn_output,
            "thinking_tokens": prev_session.get("thinking_tokens", 0) + turn_thinking,
            "cache_read_tokens": prev_session.get("cache_read_tokens", 0) + cache_read_tokens,
            "total_tokens": prev_session.get("total_tokens", 0) + turn_total,
            "num_turns": prev_session.get("num_turns", 0) + 1,
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
        set_user_conversation(user_id, new_conv_id)

    # Persist session state to local disk immediately (crash/disconnect resilient)
    save_state()

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

    # Continual Listening & Runtime Evolution Reviewer in background
    if response_text and not response_text.startswith("❌"):
        try:
            # 1. Legacy fast memory extractor
            asyncio.create_task(asyncio.to_thread(monitor_and_extract, prompt, response_text))
            # 2. Hermes-style durable Evolution Queue (durable trajectory analysis)
            from evolution.queue import enqueue_turn
            traj = {
                "user_prompt": prompt,
                "agent_response": response_text,
                "backend": "agy",
                "timestamp": int(time.time()),
                "user_id": user_id
            }
            enqueue_turn(user_id, traj)
        except Exception as e:
            # The queue's whole point is zero-loss review jobs across restarts.
            logger.error("Review job NOT enqueued, this turn will never be reviewed: %s: %s",
                         type(e).__name__, e)
        # Record into the rolling cross-backend transcript for handoff
        append_transcript(user_id, "user", "agy", prompt)
        append_transcript(user_id, "assistant", "agy", response_text)

    return response_text, new_conv_id, turn_usage


async def run_agent_turn(
    prompt: str,
    user_id: int,
    on_progress: Optional[Callable[[str], Coroutine]] = None,
) -> Tuple[str, Optional[str], Optional[dict]]:
    """
    Execute a turn, routing to agy or local OpenCode by the user's model.

    Agy-model turns automatically fall back to local OpenCode once when the
    agy backend errors, times out, or returns empty.

    Returns:
        (response_text, new_conversation_id, turn_usage)
    """
    model = get_user_model(user_id)

    if is_opencode_model(model):
        from opencode_runner import run_opencode_turn
        _last_backend[user_id] = "opencode"
        return await run_opencode_turn(
            prompt=prompt,
            user_id=user_id,
            model=model,
            on_progress=on_progress,
        )

    try:
        reply_text, new_conv_id, turn_usage = await _run_agy_turn(
            prompt=prompt,
            user_id=user_id,
            on_progress=on_progress,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("agy turn raised for user %s: %s", user_id, e)
        raise

    return reply_text, new_conv_id, turn_usage


async def compact_user_conversation(
    user_id: int,
    on_progress: Optional[Callable[[str], Coroutine]] = None,
) -> Tuple[bool, str, int, int]:
    """
    Compress active conversation context into a structured summary
    and seamlessly seed a fresh conversation session.

    Cross-backend aware: the summary is generated on the backend that
    actually holds the session (which may be the OTHER backend after a
    model switch), then the fresh session is seeded on the CURRENT backend
    — so /compact also migrates memory across backends.

    Returns:
        (success, message/summary, old_total_tokens, new_total_tokens)
    """
    oc_mode = is_opencode_model(get_user_model(user_id))
    agy_conv = get_user_conversation(user_id)
    oc_sess = get_user_oc_session(user_id)

    # Pick the backend that actually holds memory as the summary source.
    if oc_mode:
        source = "opencode" if oc_sess else ("agy" if agy_conv else None)
    else:
        source = "agy" if agy_conv else ("opencode" if oc_sess else None)

    if source is None:
        return False, "目前尚未建立任何會話記憶，無需壓縮。", 0, 0

    oc_model_arg = get_user_model(user_id) if oc_mode else None

    old_session = user_session_usage.get(user_id, {})
    old_tokens = old_session.get("total_tokens", 0)

    source_label = "本地 OpenCode" if source == "opencode" else "Antigravity"
    if on_progress:
        await on_progress(f"🧠 正在從 {source_label} 會話分析與提煉核心記憶...")

    # 1. Ask the SOURCE backend (the one holding the session) to summarize
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

    if source == "opencode":
        from opencode_runner import run_opencode_turn
        from ui_components import OPENCODE_MODELS

        # Reliability chain: try the preferred/last-working OC model first,
        # then the env default, then every known OC model — a single model's
        # context limit or quota hiccup must not break /compact.
        candidates: List[str] = []
        for cand in [oc_model_arg or get_user_oc_model(user_id), OPENCODE_DEFAULT_MODEL] + [
            m["id"] for m in OPENCODE_MODELS
        ]:
            if cand and cand not in candidates:
                candidates.append(cand)

        summary_reply = None
        last_error = ""
        for cand in candidates:
            if on_progress:
                await on_progress(f"🧠 正在從 本地 OpenCode 會話提煉記憶（模型: {cand}）...")
            reply, _, _ = await run_opencode_turn(
                prompt=summary_prompt,
                user_id=user_id,
                model=cand,
                on_progress=on_progress,
            )
            if reply and not reply.startswith("❌"):
                summary_reply = reply
                break
            last_error = reply or ""
            logger.warning("compact summary failed with OC model %s: %s", cand, last_error[:200])
        if not summary_reply:
            summary_reply = last_error
    else:
        summary_reply, _, _ = await _run_agy_turn(
            prompt=summary_prompt,
            user_id=user_id,
            on_progress=on_progress,
        )

    if not summary_reply or summary_reply.startswith("❌"):
        return False, f"提煉對話記憶失敗：\n{summary_reply}", old_tokens, 0

    # 2. Reset BOTH backends' sessions — all memory now migrates into the
    # freshly seeded session on the current backend.
    reset_user_conversation(user_id)
    reset_user_oc_session(user_id)

    if on_progress:
        await on_progress("🔄 正在注入壓縮記憶至全新會話...")

    # 3. Seed new session on the CURRENT backend with the compact summary
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

    # The seeded session is now the single source of truth; drop the rolling
    # transcript so no stale handoff context gets injected later.
    clear_transcript(user_id)

    # Persist the session summary to SESSIONS.md (short-term continuity aid,
    # kept separate from curated long-term facts in MEMORY.md)
    try:
        add_session_summary(summary_reply)
    except Exception:
        pass

    return True, summary_reply, old_tokens, new_tokens

