"""
Antigravity IDE Execution & Turn Management Engine.
===================================================
Manages interactive agent turns via Antigravity IDE over CDP.
Provides progress updates, interactive question handling, cancellation,
and dual-channel response extraction.
"""

import asyncio
import logging
import time
from typing import Callable, Coroutine, Dict, Optional, Tuple

from config import IDE_CDP_PORT, IDE_AUTO_ACCEPT
from ide_cdp import ide_controller, is_ide_cdp_online
from session_store import append_transcript, save_state

logger = logging.getLogger("agy-tg-bot.ide_runner")

# User ID -> Active IDE Task
_active_ide_tasks: Dict[int, asyncio.Task] = {}
_ide_cancelled_users: set = set()

# User ID -> Last extracted thinking content
_user_ide_thinking: Dict[int, Optional[str]] = {}

# User ID -> Pending Interactive Question dict
_user_pending_questions: Dict[int, dict] = {}


def is_ide_task_running(user_id: int) -> bool:
    """Check if an IDE task is currently active for the user."""
    t = _active_ide_tasks.get(user_id)
    return t is not None and not t.done()


async def cancel_ide_task(user_id: int) -> bool:
    """Cancel the active Antigravity IDE task by clicking Stop in the IDE UI."""
    _ide_cancelled_users.add(user_id)
    stopped = False
    try:
        stopped = await ide_controller.stop()
    except Exception as e:
        logger.warning("Error sending stop signal to Antigravity IDE: %s", e)

    t = _active_ide_tasks.get(user_id)
    if t and not t.done():
        t.cancel()
        stopped = True
    return stopped


def get_user_ide_thinking(user_id: int) -> Optional[str]:
    """Get the cached thinking content from the last IDE turn."""
    return _user_ide_thinking.get(user_id)


def get_pending_question(user_id: int) -> Optional[dict]:
    """Get any pending interactive question for the user."""
    return _user_pending_questions.get(user_id)


def clear_pending_question(user_id: int) -> None:
    """Clear any pending question state."""
    _user_pending_questions.pop(user_id, None)


async def answer_pending_question(user_id: int, answer: str) -> bool:
    """Answer the current interactive question in the IDE."""
    clear_pending_question(user_id)
    return await ide_controller.answer_question(answer)


async def run_ide_turn(
    prompt: str,
    user_id: int,
    on_progress: Optional[Callable[[str], Coroutine]] = None,
    on_question: Optional[Callable[[dict], Coroutine]] = None,
    on_learn_notify: Optional[Callable[[str], Coroutine]] = None,
    timeout_seconds: int = 1800,
) -> Tuple[str, Optional[dict]]:
    """Execute a single agent turn by injecting the prompt into Antigravity IDE.
    
    Args:
        prompt: User message text
        user_id: Telegram user ID
        on_progress: Callback to report status string
        on_question: Callback when IDE presents an interactive question/modal
        on_learn_notify: Callback when auto-learner synthesizes rules
        timeout_seconds: Maximum time to wait for agent completion
        
    Returns:
        (response_text, turn_usage)
    """
    _ide_cancelled_users.discard(user_id)
    clear_pending_question(user_id)

    # 1. Check CDP reachability
    online = await is_ide_cdp_online(ide_controller.host, ide_controller.port)
    if not online:
        return (
            f"❌ **無法連線至 Antigravity IDE (埠 {ide_controller.port})**\n\n"
            "請確認本機 Antigravity IDE 已啟動並開啟了遠端調試參數 `--remote-debugging-port`。\n\n"
            "💡 **macOS 啟動方式**：\n"
            f"```bash\nopen -a \"Antigravity IDE\" --args --remote-debugging-port={ide_controller.port}\n```\n\n"
            "或者您可使用 `/backend agy` 或 `/backend opencode` 切換至 CLI / 本地模型模式。",
            None,
        )

    if on_progress:
        try:
            await on_progress("▶ ⚡ 正在將訊息送入 Antigravity IDE...")
        except Exception:
            pass

    # 2. Inject prompt into IDE chat
    try:
        await ide_controller.send_prompt(prompt)
    except Exception as e:
        logger.error("Failed to inject prompt into Antigravity IDE: %s", e)
        return (f"❌ 發送提示詞至 Antigravity IDE 失敗: {e}", None)

    if on_progress:
        try:
            await on_progress("▶ ⚡ 訊息已送入 Antigravity IDE，等待回應...")
        except Exception:
            pass

    # 3. Monitor execution loop
    start_time = time.time()
    min_run_duration = 2.0  # Give IDE at least 2 seconds before checking for idle completion
    consecutive_idle = 0
    last_notified_state = ""
    last_synced_steps = []
    seen_question = False

    try:
        _active_ide_tasks[user_id] = asyncio.current_task()
        while time.time() - start_time < timeout_seconds:
            if user_id in _ide_cancelled_users:
                raise asyncio.CancelledError("User cancelled IDE task")

            await asyncio.sleep(1.0)
            elapsed = time.time() - start_time

            try:
                state = await ide_controller.get_state()
            except Exception as e:
                logger.debug("Failed querying IDE state: %s", e)
                continue

            is_gen = state.get("is_generating", False)
            is_loading = state.get("is_loading", False)
            has_response = state.get("has_response", False)
            all_steps = state.get("all_steps", [])
            current_step = state.get("current_step")
            question = state.get("question")
            auto_accepted = state.get("auto_accepted", 0)

            if auto_accepted > 0 and on_progress:
                try:
                    await on_progress("⚡ 已為您自動授權操作 (Auto-Accept)")
                except Exception:
                    pass

            # Handle interactive question (ask_question tool or permission dialog)
            if question and not seen_question:
                seen_question = True
                _user_pending_questions[user_id] = question
                if on_question:
                    try:
                        await on_question(question)
                    except Exception as e:
                        logger.error("Error invoking on_question callback: %s", e)

            # Sync live steps to Telegram progress
            if all_steps and all_steps != last_synced_steps and on_progress:
                last_synced_steps = list(all_steps)
                consecutive_idle = 0
                try:
                    await on_progress(f"SYNC:{json.dumps(all_steps)}")
                except Exception:
                    pass
            elif current_step and current_step != last_notified_state and on_progress:
                consecutive_idle = 0
                last_notified_state = current_step
                try:
                    await on_progress(current_step)
                except Exception:
                    pass
            elif is_gen or is_loading:
                consecutive_idle = 0
            else:
                # If neither generating nor loading
                if elapsed >= min_run_duration and not question:
                    consecutive_idle += 1
                    # If response is already rendered in DOM, complete after 2 idle ticks
                    if has_response and consecutive_idle >= 2:
                        break
                    # If no response yet but completely idle for 4 ticks, complete
                    if consecutive_idle >= 4:
                        break

    except asyncio.CancelledError:
        logger.info("IDE turn for user %s was cancelled", user_id)
        return ("🛑 任務已由使用者手動中止 (Cancelled)", None)
    finally:
        _active_ide_tasks.pop(user_id, None)

    # 4. Extract latest response
    duration = max(0.1, time.time() - start_time)
    response_text, thinking = await ide_controller.get_latest_response()
    if thinking:
        _user_ide_thinking[user_id] = thinking

    if not response_text:
        if last_synced_steps:
            response_text = "✓ Antigravity IDE 已完成操作：\n\n" + "\n".join(last_synced_steps[-6:])
        else:
            response_text = "✓ Antigravity IDE 已完成操作，請在 IDE 視窗中查看結果。"


    # 5. Record to transcript
    append_transcript(user_id, "user", "ide", prompt)
    append_transcript(user_id, "assistant", "ide", response_text)

    # 6. Continuous Auto-Learner: check for corrections and persist learnings
    try:
        from learner import auto_learn_from_turn
        auto_learn_from_turn(user_id, prompt, response_text, notify_callback=on_learn_notify)
    except Exception as le:
        logger.debug("Auto-learn check error in IDE turn: %s", le)

    # IDE CDP doesn't report granular tokens per turn, provide elapsed stats & steps
    turn_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "duration_seconds": round(duration, 1),
        "backend": "ide",
        "steps": last_synced_steps,
    }
    return response_text, turn_usage

