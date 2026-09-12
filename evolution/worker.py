"""
Background Review Worker.
Continuously consumes review jobs from the SQLite queue,
evaluates trajectories with pure functions, and dispatches via the policy gate.
Zero sandboxes; zero delay to main chat flow.
"""
import asyncio
import json
import logging
import time
from typing import Optional, Any

from evolution.queue import pop_pending_job, mark_job_status
from evolution.evaluator import evaluate_trajectory
from evolution.gate import dispatch_candidate
from evolution.lifecycle import run_lifecycle_pass

logger = logging.getLogger("agy-tg-bot.evolution.worker")

_worker_running = False
_bot_instance: Optional[Any] = None

def set_bot_instance(bot: Any) -> None:
    global _bot_instance
    _bot_instance = bot

def evaluator_available() -> bool:
    """Is there an auxiliary model the reviewer can actually call?"""
    from evolution.evaluator import SENSENOVA_API_KEY, OPENROUTER_API_KEY
    return bool(SENSENOVA_API_KEY or OPENROUTER_API_KEY)


async def start_evolution_worker() -> None:
    """Main non-blocking async worker loop."""
    global _worker_running
    if _worker_running:
        return

    # Without an auxiliary key evaluate_trajectory() returns [] every time, so
    # the loop would poll SQLite every 3s forever to do nothing. Queued jobs are
    # durable — they stay pending and are picked up once a key is configured.
    if not evaluator_available():
        logger.warning(
            "⏸️ Evolution Worker not started: neither SENSENOVA_API_KEY nor "
            "OPENROUTER_API_KEY is set. Review jobs will queue durably and be "
            "processed once a key is configured. Bot功能不受影響。"
        )
        return

    _worker_running = True
    logger.info("🚀 Background Evolution Worker started")

    last_lifecycle_check = 0

    while _worker_running:
        try:
            # 1. Process pending jobs from SQLite queue
            job = pop_pending_job()
            if job:
                job_id = job["job_id"]
                user_id = job.get("user_id")
                try:
                    trajectory = json.loads(job["trajectory_json"])
                    # Run pure functional evaluation
                    candidates = await evaluate_trajectory(trajectory)
                    for cand in candidates:
                        await dispatch_candidate(cand, bot=_bot_instance, notify_chat_id=user_id)
                    mark_job_status(job_id, "completed")
                    logger.info("✅ [Worker] Review completed for %s (%d candidates)", job_id, len(candidates))
                except Exception as je:
                    logger.warning("❌ [Worker] Error processing job %s: %s", job_id, je)
                    mark_job_status(job_id, "failed", inc_retry=True)

            # 2. Hourly deterministic lifecycle check
            now = int(time.time())
            if now - last_lifecycle_check > 3600:
                last_lifecycle_check = now
                stats = run_lifecycle_pass()
                if stats["stale"] or stats["archived"]:
                    logger.info("📦 [Lifecycle] Pass summary: %s", stats)

        except Exception as e:
            logger.error("Error in evolution worker loop: %s", e)

        # Sleep between checks to yield event loop
        await asyncio.sleep(3)

def stop_evolution_worker() -> None:
    global _worker_running
    _worker_running = False
    logger.info("🛑 Background Evolution Worker stopped")
