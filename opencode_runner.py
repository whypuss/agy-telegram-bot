"""
Local OpenCode Execution Engine (agy fallback backend).

Features:
- Runs `opencode run --format json` non-interactively with explicit -m model
  (never relies on the global opencode.json default, which may point at a
  down external proxy).
- Session persistence via `opencode run -s <sessionID>`.
- Streams NDJSON events for real-time progress indicators.
- Task cancellation support shared with agent_runner's per-user registry.
"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable, Coroutine, Dict, List, Optional, Tuple

from config import (
    OPENCODE_PATH,
    OPENCODE_TIMEOUT,
    OPENCODE_DEFAULT_MODEL,
    WORKSPACE_DIR,
    AGENT_SYSTEM_PROMPT,
)

logger = logging.getLogger("agy-tg-bot.opencode")

from session_store import (
    user_session_usage,
    user_last_turn_usage,
    user_lifetime_usage,
    get_user_oc_session,
    set_user_oc_session,
    get_user_oc_model,
    append_transcript,
    build_handoff_context,
    save_state,
)
from memory_manager import build_memory_context, monitor_and_extract

# User ID -> Active opencode subprocess (cancelled via agent_runner too)
_oc_active_processes: Dict[int, asyncio.subprocess.Process] = {}

# Users whose running process was deliberately terminated via cancel_oc_task
# (user cancel / correction steer). Used to convert the resulting non-zero
# exit into asyncio.CancelledError instead of a spurious "執行失敗" message.
_oc_cancelled_users: set = set()


def is_oc_task_running(user_id: int) -> bool:
    """Check if an opencode task is currently executing for a user."""
    proc = _oc_active_processes.get(user_id)
    return proc is not None and proc.returncode is None


async def cancel_oc_task(user_id: int) -> bool:
    """Cancel and terminate any running opencode task for the user."""
    proc = _oc_active_processes.get(user_id)
    if not proc or proc.returncode is not None:
        return False
    logger.info("Cancelling active opencode task for user %s (PID %s)", user_id, proc.pid)
    _oc_cancelled_users.add(user_id)
    try:
        proc.terminate()
        for _ in range(20):
            if proc.returncode is not None:
                break
            await asyncio.sleep(0.1)
        if proc.returncode is None:
            proc.kill()
    except ProcessLookupError:
        pass
    except Exception as e:
        logger.warning("Error terminating opencode process for user %s: %s", user_id, e)
    finally:
        _oc_active_processes.pop(user_id, None)
    return True


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

_PATH_RE = re.compile(r"(/(?:[\w\-.~ ]+/)*[\w\-.~ ]+\.\w{2,5})")


def _extract_existing_files(prompt: str, limit: int = 5) -> List[str]:
    """Find absolute file paths mentioned in the prompt that exist on disk."""
    found: List[str] = []
    for match in _PATH_RE.finditer(prompt):
        candidate = match.group(1).strip()
        try:
            if os.path.isfile(candidate) and candidate not in found:
                found.append(candidate)
                if len(found) >= limit:
                    break
        except Exception:
            continue
    return found


def _parse_tool_to_indicator(tool_name: str, active: bool) -> str:
    """Map an opencode tool name to a user-friendly indicator."""
    name = (tool_name or "").lower()
    if "bash" in name or "command" in name or "shell" in name:
        desc = "執行終端指令"
    elif "read" in name or "view" in name:
        desc = "讀取檔案"
    elif "write" in name or "edit" in name or "apply" in name or "patch" in name:
        desc = "編輯寫入檔案"
    elif "grep" in name or "glob" in name or "search" in name or "find" in name:
        desc = "檢索代碼/檔案"
    elif "web" in name or "fetch" in name or "browser" in name:
        desc = "聯網搜尋檢索"
    elif "todowrite" in name or "todo" in name:
        desc = "規劃任務清單"
    else:
        desc = f"調用工具 `{tool_name}`" if tool_name else "調用工具"
    return f"▶ 🔧 {desc} (OpenCode)" if active else f"✓ 🔧 {desc}"


# ---------------------------------------------------------------------------
# Core Execution Engine
# ---------------------------------------------------------------------------

async def run_opencode_turn(
    prompt: str,
    user_id: int,
    model: Optional[str] = None,
    on_progress: Optional[Callable[[str], Coroutine]] = None,
) -> Tuple[str, Optional[str], Optional[dict]]:
    """
    Execute a turn with the local OpenCode backend.

    Returns:
        (response_text, session_id, turn_usage)
    """
    oc_model = (model or get_user_oc_model(user_id) or OPENCODE_DEFAULT_MODEL).strip()
    session_id = get_user_oc_session(user_id)

    effective_prompt = prompt
    if not session_id:
        mem_ctx = build_memory_context()
        if mem_ctx:
            effective_prompt = f"{mem_ctx}\n{prompt}"

    # Cross-backend handoff: inject recent turns this backend missed while the
    # other backend was serving (e.g. user switched model after quota ran out).
    handoff_ctx = build_handoff_context(user_id, "opencode")
    if handoff_ctx:
        effective_prompt = f"{handoff_ctx}\n\n{effective_prompt}"
    if AGENT_SYSTEM_PROMPT:
        effective_prompt = f"[系統指示: {AGENT_SYSTEM_PROMPT}]\n{effective_prompt}"

    cmd = [OPENCODE_PATH, "run", "--format", "json", "-m", oc_model]
    if WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
        cmd.extend(["--dir", WORKSPACE_DIR])
    if session_id:
        cmd.extend(["-s", session_id])
    for fpath in _extract_existing_files(prompt):
        cmd.extend(["-f", fpath])
    cmd.append(effective_prompt)

    logger.info("Executing opencode for user %s (model=%s session=%s)", user_id, oc_model, session_id or "new")

    env = {**os.environ, "PAGER": "cat", "PYTHONUNBUFFERED": "1"}
    start_time = time.time()

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=WORKSPACE_DIR if os.path.isdir(WORKSPACE_DIR) else None,
            env=env,
        )
    except FileNotFoundError:
        return (
            f"❌ **本地 OpenCode 執行失敗**\n找不到可執行檔：`{OPENCODE_PATH}`\n"
            "💡 請檢查 `.env` 中的 `OPENCODE_PATH` 或重新安裝 opencode。",
            None,
            None,
        )

    _oc_active_processes[user_id] = proc
    _oc_cancelled_users.discard(user_id)

    text_parts: List[str] = []
    in_tokens = 0
    out_tokens = 0
    reasoning_tokens = 0
    cache_read = 0
    new_session_id: Optional[str] = session_id
    error_events: List[str] = []
    stderr_lines: List[str] = []

    async def _stream_stdout():
        nonlocal in_tokens, out_tokens, reasoning_tokens, cache_read, new_session_id
        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break
            line_str = line_bytes.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue
            try:
                event = json.loads(line_str)
            except Exception:
                continue
            if not isinstance(event, dict):
                continue

            sid = event.get("sessionID")
            if sid and not new_session_id:
                new_session_id = sid

            etype = event.get("type")
            part = event.get("part") or {}

            if etype == "text" and isinstance(part, dict):
                text = part.get("text") or ""
                if text:
                    text_parts.append(text)
                    if on_progress:
                        try:
                            await on_progress("▶ 📝 正在生成回答... (OpenCode)")
                        except Exception:
                            pass
            elif etype == "tool_use" and isinstance(part, dict):
                indicator = _parse_tool_to_indicator(part.get("tool", ""), active=True)
                if indicator and on_progress:
                    try:
                        await on_progress(indicator)
                    except Exception:
                        pass
            elif etype == "step_finish" and isinstance(part, dict):
                toks = part.get("tokens") or {}
                try:
                    in_tokens_local = int(toks.get("input", 0))
                    out_tokens_local = int(toks.get("output", 0))
                except (TypeError, ValueError):
                    in_tokens_local, out_tokens_local = 0, 0
                in_tokens += in_tokens_local
                out_tokens += out_tokens_local
                try:
                    reasoning_tokens += int(toks.get("reasoning", 0))
                except (TypeError, ValueError):
                    pass
                try:
                    cache = toks.get("cache") or {}
                    cache_read += int(cache.get("read", 0))
                except (TypeError, ValueError):
                    pass
                if on_progress:
                    try:
                        await on_progress("✓ 🧠 推理步驟完成 (OpenCode)")
                    except Exception:
                        pass
            elif etype == "error":
                err = event.get("error") or {}
                msg = err.get("message") if isinstance(err, dict) else str(err)
                if msg:
                    error_events.append(str(msg)[:500])

    async def _stream_stderr():
        while True:
            line_bytes = await proc.stderr.readline()
            if not line_bytes:
                break
            decoded = line_bytes.decode("utf-8", errors="replace").rstrip()
            if decoded:
                stderr_lines.append(decoded)
                if on_progress and ("error" in decoded.lower() or "warn" in decoded.lower()):
                    try:
                        await on_progress(f"⚠️ {decoded[:90]}")
                    except Exception:
                        pass

    stderr_task = asyncio.create_task(_stream_stderr())
    stdout_task = asyncio.create_task(_stream_stdout())

    try:
        await asyncio.wait_for(
            asyncio.gather(stderr_task, stdout_task, proc.wait()),
            timeout=OPENCODE_TIMEOUT + 15,
        )
    except asyncio.CancelledError:
        logger.info("OpenCode task cancelled for user %s", user_id)
        await cancel_oc_task(user_id)
        raise
    except asyncio.TimeoutError:
        logger.warning("OpenCode task timed out for user %s", user_id)
        await cancel_oc_task(user_id)
        raise asyncio.TimeoutError(f"OpenCode 執行超時（{OPENCODE_TIMEOUT} 秒）")
    finally:
        if not stderr_task.done():
            stderr_task.cancel()
        if not stdout_task.done():
            stdout_task.cancel()
        _oc_active_processes.pop(user_id, None)

    # Process was deliberately terminated (user cancel / correction steer):
    # surface as a cancellation so the bot layer can stay silent or re-merge,
    # instead of showing a spurious "Exit Code: -15" failure.
    if user_id in _oc_cancelled_users:
        _oc_cancelled_users.discard(user_id)
        logger.info("OpenCode task for user %s terminated by cancellation", user_id)
        raise asyncio.CancelledError()

    duration = max(0.1, time.time() - start_time)
    response_text = "".join(text_parts).strip()

    turn_usage: Optional[dict] = None
    total_tokens = in_tokens + out_tokens
    if total_tokens > 0 or response_text:
        turn_usage = {
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "thinking_tokens": reasoning_tokens,
            "cache_read_tokens": cache_read,
            "total_tokens": total_tokens,
            "duration_seconds": duration,
        }
        user_last_turn_usage[user_id] = turn_usage
        prev = user_session_usage.get(user_id, {})
        user_session_usage[user_id] = {
            "input_tokens": prev.get("input_tokens", 0) + in_tokens,
            "output_tokens": prev.get("output_tokens", 0) + out_tokens,
            "thinking_tokens": prev.get("thinking_tokens", 0) + reasoning_tokens,
            "cache_read_tokens": prev.get("cache_read_tokens", 0) + cache_read,
            "total_tokens": prev.get("total_tokens", 0) + total_tokens,
            "num_turns": prev.get("num_turns", 0) + 1,
        }
        lifetime = user_lifetime_usage.setdefault(
            user_id,
            {"turns": 0, "total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0},
        )
        lifetime["turns"] += 1
        lifetime["total_tokens"] += total_tokens
        lifetime["input_tokens"] += in_tokens
        lifetime["output_tokens"] += out_tokens
        lifetime["thinking_tokens"] += reasoning_tokens

    if new_session_id:
        set_user_oc_session(user_id, new_session_id)
    save_state()

    if not response_text:
        detail = "; ".join(error_events) or "\n".join(stderr_lines[-5:])
        if proc.returncode not in (0, None) or error_events:
            lines = [f"❌ **OpenCode 執行失敗 (Exit Code: {proc.returncode})**"]
            if detail.strip():
                lines.append(f"\n🔍 **錯誤詳情：**\n```\n{detail.strip()[:800]}\n```")
            else:
                lines.append("\n⚠️ 本地進程未返回詳細錯誤，可重試或切換模型後再試。")
            response_text = "\n".join(lines)
        else:
            response_text = "（OpenCode 回覆為空）"

    if response_text and not response_text.startswith("❌"):
        try:
            asyncio.create_task(asyncio.to_thread(monitor_and_extract, prompt, response_text))
        except Exception:
            pass
        # Record into the rolling cross-backend transcript for handoff
        append_transcript(user_id, "user", "opencode", prompt)
        append_transcript(user_id, "assistant", "opencode", response_text)

    return response_text, new_session_id, turn_usage
