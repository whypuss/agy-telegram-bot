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

# User ID -> Active Session Usage dict (cumulative for active conversation)
user_session_usage: Dict[int, dict] = {}

# User ID -> Last Turn Usage dict (delta for the latest single turn)
user_last_turn_usage: Dict[int, dict] = {}

# User ID -> Lifetime Usage dict (total across all sessions since bot startup)
user_lifetime_usage: Dict[int, dict] = {}


def load_state() -> None:
    """Load session state from disk on startup."""
    global user_conversations, user_models, user_session_usage, user_last_turn_usage, user_lifetime_usage
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
            user_session_usage = _int_dict(data.get("session_usage", {}))
            user_last_turn_usage = _int_dict(data.get("last_turn_usage", {}))
            user_lifetime_usage = _int_dict(data.get("lifetime_usage", {}))

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
            "session_usage": {str(k): v for k, v in user_session_usage.items()},
            "last_turn_usage": {str(k): v for k, v in user_last_turn_usage.items()},
            "lifetime_usage": {str(k): v for k, v in user_lifetime_usage.items()},
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
    """Set the model for a user and reset their conversation context."""
    user_models[user_id] = model_name
    reset_user_conversation(user_id)
    save_state()


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


# Initial load when module is imported
load_state()
