"""
Antigravity CLI (agy) Execution & Process Management Engine.

Features:
- Executes agy CLI turns with conversation persistence (--conversation)
- Asynchronous stderr streaming for real-time progress parsing
- Task cancellation support (terminating and killing running child processes)
- Robust error extraction and timeout handling
"""

import asyncio
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
    """Reset the conversation context for a user."""
    user_conversations.pop(user_id, None)


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

def _parse_stderr_line_to_indicator(line: str) -> Optional[str]:
    """Parse a single stderr line from agy into a user-friendly status indicator."""
    if not line or not line.strip():
        return None
    
    # Skip noisy HTTP logging
    if "httpx" in line or ("202" in line and "POST" in line):
        return None

    line_clean = line.strip()
    line_lower = line_clean.lower()

    if "tool" in line_lower or "executing" in line_lower or "running" in line_lower:
        # Extract command or tool summary if possible
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
) -> Tuple[str, Optional[str]]:
    """
    Execute a turn of the Antigravity Agent for a user.
    
    Args:
        prompt: User input prompt (may include media references)
        user_id: Telegram user ID
        on_progress: Async callback invoked with progress indicator strings
        
    Returns:
        (response_text, new_conversation_id)
    """
    model = get_user_model(user_id)
    conv_id = get_user_conversation(user_id)

    cmd = [AGY_PATH]
    cmd.extend(["--print-timeout", f"{AGY_TIMEOUT}s"])
    cmd.extend(["--dangerously-skip-permissions"])
    cmd.extend(["--output-format", "text"])

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

    # Close stdin since prompt is passed via CLI flag
    if proc.stdin:
        try:
            proc.stdin.close()
        except Exception:
            pass

    async def _stream_stderr():
        while True:
            line_bytes = await proc.stderr.readline()
            if not line_bytes:
                break
            decoded = line_bytes.decode("utf-8", errors="replace").rstrip()
            stderr_lines.append(decoded)
            
            indicator = _parse_stderr_line_to_indicator(decoded)
            if indicator and on_progress:
                try:
                    await on_progress(indicator)
                except Exception:
                    pass

    async def _read_stdout() -> bytes:
        return await proc.stdout.read()

    stderr_task = asyncio.create_task(_stream_stderr())
    stdout_task = asyncio.create_task(_read_stdout())

    try:
        await asyncio.wait_for(
            asyncio.gather(stderr_task, stdout_task, proc.wait()),
            timeout=AGY_TIMEOUT + 15,
        )
        stdout_bytes = stdout_task.result()
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

    response_text = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr_text = "\n".join(stderr_lines)

    # Extract Conversation UUID from stderr logs
    new_conv_id = None
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
        if proc.returncode != 0:
            logger.warning("agy exited with code %s. stderr: %s", proc.returncode, stderr_text[:500])
            response_text = f"❌ *Agent 執行失敗 (Exit Code: {proc.returncode})*\n\n```\n{stderr_text[:800]}\n```"
        else:
            response_text = "（Agent 回覆為空）"

    return response_text, new_conv_id
