"""
SQLite-backed durable review queue for post-turn reflection.
Ensures zero-loss of review jobs across bot restarts.
"""
import sqlite3
import json
import time
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Dict, Any

from config import MEMORY_DIR

logger = logging.getLogger("agy-tg-bot.evolution.queue")

DB_PATH = MEMORY_DIR / "evolution_jobs.db"

_initialized = False


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """Open a connection that is committed on success and always closed.

    `with sqlite3.connect(...)` alone only manages the transaction — it leaks
    the file descriptor. Every caller must go through here.
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_queue() -> None:
    """Initialize the SQLite queue table (idempotent, runs once per process)."""
    global _initialized
    if _initialized:
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS review_jobs (
                job_id TEXT PRIMARY KEY,
                user_id INTEGER,
                trajectory_json TEXT,
                status TEXT DEFAULT 'pending', -- pending, running, completed, failed
                retry_count INTEGER DEFAULT 0,
                created_at INTEGER,
                updated_at INTEGER
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status_created ON review_jobs (status, created_at)")
    _initialized = True

def enqueue_turn(user_id: int, trajectory: Dict[str, Any]) -> str:
    """Enqueue a conversation turn for asynchronous review."""
    init_queue()
    job_id = f"job_{int(time.time() * 1000)}_{user_id}"
    now = int(time.time())
    with _db() as conn:
        conn.execute(
            """INSERT INTO review_jobs
               (job_id, user_id, trajectory_json, status, retry_count, created_at, updated_at)
               VALUES (?, ?, ?, 'pending', 0, ?, ?)""",
            (job_id, user_id, json.dumps(trajectory, ensure_ascii=False), now, now)
        )
    logger.info("Enqueued turn review job: %s (user %s)", job_id, user_id)
    return job_id

def pop_pending_job() -> Optional[Dict[str, Any]]:
    """Atomically pop the oldest pending job and mark as running."""
    init_queue()
    now = int(time.time())
    with _db() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM review_jobs WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1")
        row = cur.fetchone()
        if row:
            job = dict(row)
            conn.execute("UPDATE review_jobs SET status = 'running', updated_at = ? WHERE job_id = ?",
                         (now, job["job_id"]))
            return job
    return None

def mark_job_status(job_id: str, status: str, inc_retry: bool = False) -> None:
    """Update job status and retry counter."""
    now = int(time.time())
    retry_clause = "retry_count = retry_count + 1," if inc_retry else ""
    with _db() as conn:
        conn.execute(
            f"UPDATE review_jobs SET {retry_clause} status = ?, updated_at = ? WHERE job_id = ?",
            (status, now, job_id)
        )

def cleanup_old_jobs(max_age_days: int = 14) -> int:
    """Remove completed/failed jobs older than max_age_days."""
    cutoff = int(time.time()) - (max_age_days * 86400)
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM review_jobs WHERE status IN ('completed', 'failed') AND updated_at < ?", (cutoff,))
        deleted = cur.rowcount
    return deleted
