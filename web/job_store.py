"""
Job store — SQLite-backed job state with asyncio confirm gate.

Each job has:
  - id (UUID)
  - status: queued | running | confirm_pending | done | error
  - project_path: path to the generated project directory
  - log: accumulated text log
  - confirm_data: JSON recommendations from agent (when status=confirm_pending)
  - confirm_result: JSON choices submitted by user
  - error_message: set on failure
  - created_at / updated_at

The confirm gate uses a per-job asyncio.Event so the agent runner can
await user confirmation without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path("/app/projects/.job_store.db")


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'queued',
                project_path TEXT,
                log TEXT NOT NULL DEFAULT '',
                confirm_data TEXT,
                confirm_result TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()


_init_db()

# Per-job asyncio events for the confirm gate
_confirm_events: dict[str, asyncio.Event] = {}

# Per-job log queues for WebSocket streaming
_log_queues: dict[str, asyncio.Queue] = {}


class JobStore:
    def create_job(self) -> str:
        job_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO jobs (id, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (job_id, "queued", now, now),
            )
            conn.commit()
        _confirm_events[job_id] = asyncio.Event()
        _log_queues[job_id] = asyncio.Queue()
        return job_id

    def get_job(self, job_id: str) -> Optional[dict]:
        with _get_conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return dict(row)

    def set_status(self, job_id: str, status: str) -> None:
        now = datetime.utcnow().isoformat()
        with _get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, job_id),
            )
            conn.commit()

    def set_project_path(self, job_id: str, path: str) -> None:
        now = datetime.utcnow().isoformat()
        with _get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET project_path = ?, updated_at = ? WHERE id = ?",
                (path, now, job_id),
            )
            conn.commit()

    def set_error(self, job_id: str, message: str) -> None:
        now = datetime.utcnow().isoformat()
        with _get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'error', error_message = ?, updated_at = ? WHERE id = ?",
                (message, now, job_id),
            )
            conn.commit()

    async def append_log(self, job_id: str, text: str) -> None:
        """Append text to the job log and push to the WebSocket queue."""
        now = datetime.utcnow().isoformat()
        with _get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET log = log || ?, updated_at = ? WHERE id = ?",
                (text, now, job_id),
            )
            conn.commit()
        if job_id in _log_queues:
            await _log_queues[job_id].put({"type": "log", "text": text})

    async def push_event(self, job_id: str, event: dict) -> None:
        """Push a structured event to the WebSocket queue."""
        if job_id in _log_queues:
            await _log_queues[job_id].put(event)

    async def get_next_event(self, job_id: str) -> Optional[dict]:
        """Get next event from the queue (used by WebSocket handler)."""
        if job_id not in _log_queues:
            return None
        try:
            return await asyncio.wait_for(_log_queues[job_id].get(), timeout=30.0)
        except asyncio.TimeoutError:
            return None

    # ── Confirm gate ──────────────────────────────────────────────────────────

    def set_confirm_pending(self, job_id: str, recommendations: Any) -> None:
        now = datetime.utcnow().isoformat()
        with _get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'confirm_pending', confirm_data = ?, updated_at = ? WHERE id = ?",
                (json.dumps(recommendations), now, job_id),
            )
            conn.commit()

    async def wait_for_confirm(self, job_id: str) -> None:
        """Block the agent runner until the user submits confirmation."""
        if job_id not in _confirm_events:
            _confirm_events[job_id] = asyncio.Event()
        await _confirm_events[job_id].wait()

    def submit_confirm(self, job_id: str, result: Any) -> None:
        """Called by the HTTP handler when user submits Eight Confirmations."""
        now = datetime.utcnow().isoformat()
        with _get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'running', confirm_result = ?, updated_at = ? WHERE id = ?",
                (json.dumps(result), now, job_id),
            )
            conn.commit()
        if job_id in _confirm_events:
            _confirm_events[job_id].set()

    def get_confirm_result(self, job_id: str) -> Any:
        job = self.get_job(job_id)
        if job and job["confirm_result"]:
            return json.loads(job["confirm_result"])
        return {}

    def cleanup(self, job_id: str) -> None:
        _confirm_events.pop(job_id, None)
        _log_queues.pop(job_id, None)
