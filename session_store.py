"""
Session State Persistence Module (Hermes-inspired Crash-Resilience).

Stores conversation IDs, selected models, and token usage metrics to disk
using atomic writes. Ensures bot crashes, process restarts, or network
disconnects do not wipe user session state.
"""

import json
import logging
import os
import tempfile
import threading
from typing import Dict, Optional

from config import DEFAULT_MODEL, STATE_FILE

logger = logging.getLogger("agy-tg-bot.state")

_state_lock = threading.Lock()

# User ID -> Active Conversation ID
user_conversations: Dict[int, str] = {}

# User ID -> Selected Model
user_models: Dict[int, str] = {}

# User ID -> Active OpenCode Session ID (separate from agy conversations)
user_oc_sessions: Dict[int, str] = {}

# User ID -> Preferred OpenCode model
user_oc_models: Dict[int, str] = {}

# User ID -> Active Session Usage dict (cumulative for active conversation)
user_session_usage: Dict[int, dict] = {}

# User ID -> Last Turn Usage dict (delta for the latest single turn)
user_last_turn_usage: Dict[int, dict] = {}

# User ID -> Lifetime Usage dict (total across all sessions since bot startup)
user_lifetime_usage: Dict[int, dict] = {}

# User ID -> Rolling cross-backend transcript. Each entry:
# {"role": "user"|"assistant", "backend": "agy"|"opencode", "text": str}
# Used to hand off recent context when the user switches backends
# (native sessions of agy and OpenCode are incompatible and isolated).
user_transcripts: Dict[int, list] = {}

TRANSCRIPT_MAX_ENTRIES = 12
TRANSCRIPT_TEXT_LIMIT = 1000

_BACKEND_LABELS = {"agy": "Antigravity", "opencode": "本地 OpenCode"}


def load_state() -> None:
    """Load session state from disk on startup."""
    global user_conversations, user_models, user_session_usage, user_last_turn_usage, user_lifetime_usage
    global user_oc_sessions, user_oc_models, user_transcripts
    with _state_lock:
        if not STATE_FILE.exists():
            logger.info("No existing state file found at %s. Initializing fresh state.", STATE_FILE)
            return

        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                return

            def _int_dict(d: dict) -> dict:
                res = {}
                for k, v in d.items():
                    try:
                        res[int(k)] = v
                    except (ValueError, TypeError):
                        pass
                return res

            user_conversations = _int_dict(data.get("conversations", {}))
            user_models = _int_dict(data.get("models", {}))
            user_oc_sessions = _int_dict(data.get("oc_sessions", {}))
            user_oc_models = _int_dict(data.get("oc_models", {}))
            user_session_usage = _int_dict(data.get("session_usage", {}))
            user_last_turn_usage = _int_dict(data.get("last_turn_usage", {}))
            user_lifetime_usage = _int_dict(data.get("lifetime_usage", {}))
            user_transcripts = _int_dict(data.get("transcripts", {}))

            logger.info(
                "Successfully loaded state from %s: %d conversations, %d models",
                STATE_FILE,
                len(user_conversations),
                len(user_models),
            )
        except Exception as e:
            logger.error("Failed to load session state from %s: %s", STATE_FILE, e)


def save_state() -> None:
    """Save session state to disk atomically."""
    with _state_lock:
        data = {
            "conversations": {str(k): v for k, v in user_conversations.items()},
            "models": {str(k): v for k, v in user_models.items()},
            "oc_sessions": {str(k): v for k, v in user_oc_sessions.items()},
            "oc_models": {str(k): v for k, v in user_oc_models.items()},
            "session_usage": {str(k): v for k, v in user_session_usage.items()},
            "last_turn_usage": {str(k): v for k, v in user_last_turn_usage.items()},
            "lifetime_usage": {str(k): v for k, v in user_lifetime_usage.items()},
            "transcripts": {str(k): v for k, v in user_transcripts.items()},
        }

        try:
            parent = STATE_FILE.parent
            parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=str(parent), prefix="tg_bot_state_", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, STATE_FILE)
        except Exception as e:
            logger.error("Failed to persist session state to %s: %s", STATE_FILE, e)


# ---------------------------------------------------------------------------
# State Query & Modification Helpers
# ---------------------------------------------------------------------------

def get_user_model(user_id: int) -> str:
    """Get the current model for a user, or default."""
    return user_models.get(user_id, DEFAULT_MODEL)


def set_user_model(user_id: int, model_name: str) -> None:
    """Set the model for a user WITHOUT wiping conversation memory.

    Both backends' native sessions (agy conversation ID and OpenCode session
    ID) are kept intact across model/backend switches so switching back
    resumes the previous session. Context gaps created while the other
    backend was active are bridged automatically by the rolling transcript
    (see build_handoff_context), ensuring memory continuity when users
    switch models due to quota exhaustion.
    """
    user_models[user_id] = model_name
    save_state()


def append_transcript(user_id: int, role: str, backend: str, text: str) -> None:
    """Append one entry to the user's rolling cross-backend transcript."""
    text = (text or "").strip()
    if not text:
        return
    if len(text) > TRANSCRIPT_TEXT_LIMIT:
        text = text[:TRANSCRIPT_TEXT_LIMIT] + " …"
    entries = user_transcripts.setdefault(user_id, [])
    entries.append({"role": role, "backend": backend, "text": text})
    while len(entries) > TRANSCRIPT_MAX_ENTRIES:
        entries.pop(0)
    save_state()


def clear_transcript(user_id: int) -> None:
    """Drop the rolling transcript for a user (used by /reset)."""
    user_transcripts.pop(user_id, None)
    save_state()


def build_handoff_context(user_id: int, current_backend: str) -> Optional[str]:
    """Build a context-handoff block covering turns the current backend missed.

    Returns None when the current backend has already seen every recorded
    turn (steady state), so nothing extra is injected into the prompt.
    """
    entries = user_transcripts.get(user_id) or []
    if not entries:
        return None

    last_own = -1
    for i in range(len(entries) - 1, -1, -1):
        if entries[i].get("backend") == current_backend:
            last_own = i
            break
    gap = entries[last_own + 1:]
    if not gap:
        return None

    lines = []
    for e in gap:
        speaker = "用戶" if e.get("role") == "user" else "助手"
        label = _BACKEND_LABELS.get(e.get("backend"), e.get("backend") or "未知後端")
        lines.append(f"[{speaker} · {label}]: {e.get('text', '')}")

    return (
        "【上下文交接 Context Handoff】\n"
        "以下是你與使用者此前在另一後端的近期對話紀錄（因後端/模型切換，你無法直接讀取該會話）。"
        "請將其視為已發生的對話歷史，無縫承接，確保記憶連續；回答時不要重複已完成的事項：\n\n"
        + "\n\n".join(lines)
    )





def get_user_conversation(user_id: int) -> Optional[str]:
    """Get the active conversation ID for a user."""
    return user_conversations.get(user_id)


def set_user_conversation(user_id: int, conv_id: str) -> None:
    """Set the active conversation ID for a user and save state."""
    user_conversations[user_id] = conv_id
    save_state()


def reset_user_conversation(user_id: int) -> None:
    """Reset the conversation context and session usage for a user."""
    user_conversations.pop(user_id, None)
    user_session_usage.pop(user_id, None)
    user_last_turn_usage.pop(user_id, None)
    save_state()


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


def get_user_backend(user_id: int) -> str:
    """Return 'opencode' if the user's current model is an OpenCode model, else 'agy'."""
    from config import is_opencode_model
    return "opencode" if is_opencode_model(get_user_model(user_id)) else "agy"


def get_user_oc_model(user_id: int) -> str:
    """Get the user's preferred OpenCode model."""
    from config import OPENCODE_DEFAULT_MODEL
    return user_oc_models.get(user_id, OPENCODE_DEFAULT_MODEL)


def set_user_oc_model(user_id: int, model_name: str) -> None:
    """Set the user's preferred OpenCode model without touching agy state."""
    user_oc_models[user_id] = model_name
    save_state()


def get_user_oc_session(user_id: int) -> Optional[str]:
    """Get the active OpenCode session ID for a user."""
    return user_oc_sessions.get(user_id)


def set_user_oc_session(user_id: int, session_id: str) -> None:
    """Set the active OpenCode session ID for a user and save state."""
    user_oc_sessions[user_id] = session_id
    save_state()


def reset_user_oc_session(user_id: int) -> None:
    """Reset only the OpenCode session for a user."""
    user_oc_sessions.pop(user_id, None)
    save_state()


# Initial load when module is imported
load_state()
