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
    is_opencode_model,
)

logger = logging.getLogger("agy-tg-bot.opencode")

from agent_runner import (
    STREAM_LINE_LIMIT,
    _clean_and_dedup_thinking,
    _translate_thinking_to_zh_tw,
)

from session_store import (
    user_session_usage,
    user_last_turn_usage,
    user_lifetime_usage,
    get_user_oc_session,
    set_user_oc_session,
    reset_user_oc_session,
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

# User ID -> Last extracted & synthesized thinking text (for status card injection)
_user_oc_last_thinking: Dict[int, str] = {}


def get_user_oc_thinking(user_id: int) -> Optional[str]:
    """Return the last synthesized thinking process for an OpenCode user turn."""
    return _user_oc_last_thinking.get(user_id)


def set_user_oc_thinking(user_id: int, thinking: Optional[str]) -> None:
    """Store or clear the last synthesized thinking process for an OpenCode user turn."""
    if thinking:
        _user_oc_last_thinking[user_id] = thinking
    else:
        _user_oc_last_thinking.pop(user_id, None)


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
    """Find absolute file paths mentioned in the prompt that exist on disk (<= 256 KiB)."""
    found: List[str] = []
    for match in _PATH_RE.finditer(prompt):
        candidate = match.group(1).strip()
        try:
            if os.path.isfile(candidate) and candidate not in found:
                # Guard: skip massive files (> 256 KiB) to protect context window
                if os.path.getsize(candidate) <= 256 * 1024:
                    found.append(candidate)
                if len(found) >= limit:
                    break
        except Exception:
            continue
    return found


def _parse_tool_to_indicator(tool_or_part, active: bool = True) -> str:
    """Map an opencode tool name or event part to a detailed, user-friendly indicator."""
    if isinstance(tool_or_part, dict):
        part = tool_or_part
        tool_name = (part.get("tool") or "").lower()
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        status = state.get("status")
        is_completed = (status == "completed") or (not active)

        input_data = state.get("input") if isinstance(state.get("input"), dict) else (part.get("input") or {})
        if not isinstance(input_data, dict):
            input_data = {}
        title = state.get("title") or ""

        # Tool-specific details
        if "bash" in tool_name or "command" in tool_name or "shell" in tool_name:
            cmd = input_data.get("command") or title or ""
            cmd = str(cmd).strip()
            if len(cmd) > 45:
                cmd = cmd[:42] + "..."
            desc = f"執行終端指令 `{cmd}`" if cmd else "執行終端指令"
        elif "read" in tool_name or "view" in tool_name or "cat" in tool_name:
            path = input_data.get("path") or input_data.get("file_path") or title or ""
            path = os.path.basename(str(path)) if path else ""
            desc = f"讀取檔案 `{path}`" if path else "讀取檔案"
        elif "write" in tool_name or "edit" in tool_name or "apply" in tool_name or "patch" in tool_name:
            path = input_data.get("path") or input_data.get("file_path") or title or ""
            path = os.path.basename(str(path)) if path else ""
            desc = f"編輯寫入檔案 `{path}`" if path else "編輯寫入檔案"
        elif "grep" in tool_name or "glob" in tool_name or "search" in tool_name or "find" in tool_name:
            q = input_data.get("pattern") or input_data.get("query") or title or ""
            q = str(q).strip()
            if len(q) > 35:
                q = q[:32] + "..."
            desc = f"檢索代碼/檔案 `{q}`" if q else "檢索代碼/檔案"
        elif "web" in tool_name or "fetch" in tool_name or "browser" in tool_name:
            q = input_data.get("query") or input_data.get("url") or title or ""
            q = str(q).strip()
            if len(q) > 35:
                q = q[:32] + "..."
            desc = f"聯網搜尋檢索 `{q}`" if q else "聯網搜尋檢索"
        elif "todowrite" in tool_name or "todo" in tool_name:
            desc = "規劃任務清單"
        else:
            desc = f"調用工具 `{tool_name}`" if tool_name else "調用工具"

        time_info = state.get("time") if isinstance(state.get("time"), dict) else (part.get("time") or {})
        dur_str = ""
        if isinstance(time_info, dict):
            start_ms = time_info.get("start")
            end_ms = time_info.get("end")
            if start_ms and end_ms and end_ms > start_ms:
                dur_sec = (end_ms - start_ms) / 1000.0
                if dur_sec > 0.05:
                    dur_str = f" ({dur_sec:.1f}s)"

        return f"✓ 🔧 {desc}{dur_str}" if is_completed else f"▶ 🔧 {desc} (OpenCode)"

    tool_name = (str(tool_or_part) or "").lower()
    if "bash" in tool_name or "command" in tool_name or "shell" in tool_name:
        desc = "執行終端指令"
    elif "read" in tool_name or "view" in tool_name:
        desc = "讀取檔案"
    elif "write" in tool_name or "edit" in tool_name or "apply" in tool_name or "patch" in tool_name:
        desc = "編輯寫入檔案"
    elif "grep" in tool_name or "glob" in tool_name or "search" in tool_name or "find" in tool_name:
        desc = "檢索代碼/檔案"
    elif "web" in tool_name or "fetch" in tool_name or "browser" in tool_name:
        desc = "聯網搜尋檢索"
    elif "todowrite" in tool_name or "todo" in tool_name:
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
    on_learn_notify: Optional[Callable[[str], Coroutine]] = None,
    _retried: bool = False,
) -> Tuple[str, Optional[str], Optional[dict]]:
    """
    Execute a turn with the local OpenCode backend.

    Returns:
        (response_text, session_id, turn_usage)
    """
    if model and not is_opencode_model(model):
        logger.warning("Ignoring non-OpenCode model '%s' for user %s; using user oc model or default", model, user_id)
        model = None
    oc_model = (model or get_user_oc_model(user_id) or OPENCODE_DEFAULT_MODEL).strip()
    if not is_opencode_model(oc_model):
        oc_model = OPENCODE_DEFAULT_MODEL
    session_id = get_user_oc_session(user_id)

    effective_prompt = prompt
    if not session_id:
        mem_ctx = build_memory_context()
        if mem_ctx:
            effective_prompt = f"{mem_ctx}\n{prompt}"
        if AGENT_SYSTEM_PROMPT:
            effective_prompt = f"[系統指示: {AGENT_SYSTEM_PROMPT}]\n{effective_prompt}"
        effective_prompt = (
            "[執行守則：1. 思考過程（thinking）與回覆必須全程使用繁體中文進行實質推理。\n"
            "2. 本回合必須自主連續調用工具執行到底，嚴禁使用「稍後會自動執行/背景處理」等未來時態停下，直到任務全部實質落地並驗證完成。]\n"
            f"{effective_prompt}"
        )
    else:
        effective_prompt = (
            "[執行守則：1. 思考過程（thinking）與回覆必須全程使用繁體中文進行實質推理。\n"
            "2. 本回合必須自主連續調用工具執行到底，嚴禁使用「稍後會自動執行/背景處理」等未來時態停下，直到任務全部實質落地並驗證完成。]\n"
            f"{prompt}"
        )

    # Cross-backend handoff: inject recent turns this backend missed while the
    # other backend was serving (e.g. user switched model after quota ran out).
    handoff_ctx = build_handoff_context(user_id, "opencode")
    if handoff_ctx:
        effective_prompt = f"{handoff_ctx}\n\n{effective_prompt}"

    # Guard: prevent oversized input from instantly blowing up model context window
    if len(effective_prompt) > 30000:
        head = effective_prompt[:10000]
        tail = effective_prompt[-15000:]
        effective_prompt = f"{head}\n\n... [為避免超出模型上下文上限，已智慧修剪中間冗長內容] ...\n\n{tail}"

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
            # See agent_runner.STREAM_LINE_LIMIT — asyncio's 64 KiB default
            # truncates NDJSON streams mid-turn.
            limit=STREAM_LINE_LIMIT,
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
    reasoning_parts: List[str] = []
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
            try:
                line_bytes = await proc.stdout.readline()
            except Exception as e:
                logger.error("opencode stdout stream aborted: %s: %s", type(e).__name__, e)
                break
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

            meta = part.get("metadata") if isinstance(part, dict) else {}
            phase = ""
            if isinstance(meta, dict):
                openai_meta = meta.get("openai") or {}
                if isinstance(openai_meta, dict):
                    phase = openai_meta.get("phase") or ""

            if etype in ("reasoning", "thought", "thinking") and isinstance(part, dict):
                r = part.get("text") or part.get("reasoning") or part.get("thought") or ""
                if r:
                    reasoning_parts.append(r)
            elif etype == "text" and isinstance(part, dict) and phase == "commentary":
                r = part.get("text") or ""
                if r:
                    reasoning_parts.append(r)
            elif etype == "text" and isinstance(part, dict):
                text = part.get("text") or ""
                if text:
                    text_parts.append(text)
                    if on_progress:
                        try:
                            await on_progress("▶ 📝 正在生成回答... (OpenCode)")
                        except Exception:
                            pass
            elif etype == "tool_use" and isinstance(part, dict):
                indicator = _parse_tool_to_indicator(part, active=True)
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
                    r_toks_local = int(toks.get("reasoning", 0))
                    reasoning_tokens += r_toks_local
                except (TypeError, ValueError):
                    r_toks_local = 0
                try:
                    cache = toks.get("cache") or {}
                    cache_read += int(cache.get("read", 0))
                except (TypeError, ValueError):
                    pass
                if on_progress:
                    try:
                        if r_toks_local > 0:
                            await on_progress("✓ 🧠 深度思考完成 (OpenCode)")
                        else:
                            await on_progress("✓ 🧠 推理步驟完成 (OpenCode)")
                    except Exception:
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
                msg = None
                if isinstance(err, dict):
                    data = err.get("data") or {}
                    msg = (
                        err.get("message")
                        or (data.get("message") if isinstance(data, dict) else None)
                        or err.get("name")
                    )
                else:
                    msg = str(err)
                if msg:
                    error_events.append(str(msg)[:500])

    async def _stream_stderr():
        while True:
            try:
                line_bytes = await proc.stderr.readline()
            except Exception as e:
                logger.error("opencode stderr stream aborted: %s: %s", type(e).__name__, e)
                break
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

    async def _wait_oc_completion():
        await proc.wait()
        # Give streams up to 1.5s to flush remaining buffers
        try:
            await asyncio.wait_for(asyncio.gather(stderr_task, stdout_task), timeout=1.5)
        except asyncio.TimeoutError:
            pass

    try:
        await asyncio.wait_for(
            _wait_oc_completion(),
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
        if proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
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
    clean_th: Optional[str] = None
    if reasoning_parts:
        try:
            clean_th = _clean_and_dedup_thinking(reasoning_parts)
        except Exception as e:
            logger.debug("Failed cleaning opencode thinking: %s", e)
            clean_th = "\n\n".join(r.strip() for r in reasoning_parts if r.strip())

    set_user_oc_thinking(user_id, clean_th)

    if not response_text and clean_th:
        response_text = clean_th

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

    # Smart context warning when session token total approaches light model limits
    prev_total = user_session_usage.get(user_id, {}).get("total_tokens", 0)
    if prev_total >= 45000 and response_text and not response_text.startswith("❌"):
        if "compact" not in response_text[-120:].lower():
            response_text += (
                f"\n\n💡 **提示**：目前會話累積 Token 已達 {prev_total:,}，"
                "接近部分輕量模型上下文上限，建議輸入 `/compact` 壓縮記憶以保持流暢執行。"
            )

    if not response_text:
        detail = "; ".join(error_events) or "\n".join(stderr_lines[-5:])
        if proc.returncode not in (0, None) or error_events:
            # One automatic retry for transient provider errors (rate limit,
            # quota hiccups) — but never after a deliberate cancellation.
            if not _retried and user_id not in _oc_cancelled_users:
                # If context length exceeded, automatically reset session and retry with fresh condensed prompt
                is_ctx_err = any(k in detail.lower() for k in ("invalid", "context", "length", "too long", "token", "maximum"))
                if is_ctx_err and session_id:
                    logger.warning("OpenCode hit context overflow for user %s; auto-resetting session and retrying with handoff", user_id)
                    reset_user_oc_session(user_id)
                    if on_progress:
                        try:
                            await on_progress("⚠️ 會話超出模型上下文上限，正在自動重置會話並重試...")
                        except Exception:
                            pass
                    return await run_opencode_turn(
                        prompt=prompt,
                        user_id=user_id,
                        model=model,
                        on_progress=on_progress,
                        _retried=True,
                    )

                logger.warning(
                    "opencode exited %s for user %s with no output (detail: %s); retrying once",
                    proc.returncode, user_id, (detail.strip() or "<none>")[:300],
                )
                if on_progress:
                    try:
                        await on_progress("🔁 OpenCode 無輸出異常退出，自動重試一次...")
                    except Exception:
                        pass
                return await run_opencode_turn(
                    prompt=prompt,
                    user_id=user_id,
                    model=model,
                    on_progress=on_progress,
                    _retried=True,
                )
            logger.warning(
                "opencode failed for user %s: exit=%s error_events=%s stderr_tail=%s",
                user_id, proc.returncode, error_events[-3:], stderr_lines[-5:],
            )
            lines = [f"❌ **OpenCode 執行失敗 (Exit Code: {proc.returncode})**"]
            if detail.strip():
                lines.append(f"\n🔍 **錯誤詳情：**\n```\n{detail.strip()[:800]}\n```")
                if any(k in detail.lower() for k in ("invalid", "context", "length", "too long", "token")):
                    lines.append(
                        "\n💡 可能係會話上下文超出該模型嘅上下文上限，"
                        "建議使用 /compact 壓縮會話或使用 /model 切換大上下文模型後再試。"
                    )
            else:
                lines.append(
                    "\n⚠️ 本地進程未返回詳細錯誤（已自動重試一次）。\n"
                    "💡 免費模型常因限流/額度波動失敗，可稍後重試或用 /model 切換其他模型。"
                )
            response_text = "\n".join(lines)
        else:
            response_text = "（OpenCode 回覆為空）"

    if response_text and not response_text.startswith("❌"):
        try:
            asyncio.create_task(asyncio.to_thread(monitor_and_extract, prompt, response_text))
            from learner import auto_learn_from_turn
            auto_learn_from_turn(user_id, prompt, response_text, notify_callback=on_learn_notify)
        except Exception:
            pass
        # Record into the rolling cross-backend transcript for handoff
        append_transcript(user_id, "user", "opencode", prompt)
        append_transcript(user_id, "assistant", "opencode", response_text)

    return response_text, new_session_id, turn_usage
