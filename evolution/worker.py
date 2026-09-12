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

async def start_evolution_worker() -> None:
    """Main non-blocking async worker loop."""
    global _worker_running
    if _worker_running:
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
