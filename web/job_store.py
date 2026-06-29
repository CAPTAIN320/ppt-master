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
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path("/app/projects/.job_store.db")
PROJECTS_DIR = Path("/app/projects")
EXPORTS_DIR = Path("/app/exports")


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
                topic TEXT,
                slide_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        # Migrate existing DBs that lack the new columns
        existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "topic" not in existing:
            conn.execute("ALTER TABLE jobs ADD COLUMN topic TEXT")
        if "slide_count" not in existing:
            conn.execute("ALTER TABLE jobs ADD COLUMN slide_count INTEGER NOT NULL DEFAULT 0")
        conn.commit()


_init_db()

# Per-job asyncio events for the confirm gate
_confirm_events: dict[str, asyncio.Event] = {}

# Per-job asyncio events for cooperative cancellation
_cancel_events: dict[str, asyncio.Event] = {}

# Per-job log queues for WebSocket streaming
_log_queues: dict[str, asyncio.Queue] = {}


class JobStore:
    def create_job(self, topic: str = "") -> str:
        job_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO jobs (id, status, topic, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (job_id, "queued", topic, now, now),
            )
            conn.commit()
        _confirm_events[job_id] = asyncio.Event()
        _cancel_events[job_id] = asyncio.Event()
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

    def set_topic(self, job_id: str, topic: str) -> None:
        now = datetime.utcnow().isoformat()
        with _get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET topic = ?, updated_at = ? WHERE id = ?",
                (topic, now, job_id),
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

    def increment_slide_count(self, job_id: str) -> None:
        now = datetime.utcnow().isoformat()
        with _get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET slide_count = slide_count + 1, updated_at = ? WHERE id = ?",
                (now, job_id),
            )
            conn.commit()

    def list_jobs(self) -> list[dict]:
        """Return all completed (status='done') jobs sorted by creation time descending.

        Also scans the filesystem for orphan project directories (those with svg_output/
        or svg_final/ subdirectories) that have no corresponding DB entry, and includes
        them as synthetic job entries.
        """
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT id, status, topic, project_path, slide_count, created_at, updated_at "
                "FROM jobs WHERE status = 'done' ORDER BY created_at DESC"
            ).fetchall()
        result = []
        # Use directory *basenames* for deduplication so that Docker-internal paths
        # (/app/projects/foo) and host-resolved paths (/host/path/projects/foo) both
        # reduce to the same key "foo" and are not double-counted.
        known_project_names: set[str] = set()
        for row in rows:
            job = dict(row)

            job["download_url"] = f"/jobs/{job['id']}/download"

            # If slide_count is 0, try to recalculate from disk if the project
            # directory exists and has SVG files.
            if job.get("slide_count", 0) == 0 and job.get("project_path"):
                proj_dir = Path(job["project_path"])
                # Remap Docker-internal /app/projects/... to the actual PROJECTS_DIR
                # in case this process is running outside Docker (e.g. tests on host).
                if not proj_dir.exists() and proj_dir.parts[:3] == ('/', 'app', 'projects'):
                    proj_dir = PROJECTS_DIR / proj_dir.name

                if proj_dir.exists():
                    svg_dir = proj_dir / "svg_final"
                    if not svg_dir.is_dir():
                        svg_dir = proj_dir / "svg_output"
                    if svg_dir.is_dir():
                        count = len(list(svg_dir.glob("*.svg")))
                        if count > 0:
                            job["slide_count"] = count
                            # Persist the corrected count so future calls skip this work.
                            try:
                                now = datetime.utcnow().isoformat()
                                with _get_conn() as conn:
                                    conn.execute(
                                        "UPDATE jobs SET slide_count = ?, updated_at = ? WHERE id = ?",
                                        (count, now, job["id"]),
                                    )
                                    conn.commit()
                            except Exception:
                                pass  # Non-fatal; count is correct in memory for this call

            result.append(job)
            if job.get("project_path"):
                known_project_names.add(Path(job["project_path"]).name)

        # ── Orphan project directories ────────────────────────────────────────
        # Scan /app/projects/ for directories that have svg_output/ or svg_final/
        # but are not already tracked in the DB.
        if PROJECTS_DIR.exists():
            for proj_dir in sorted(PROJECTS_DIR.iterdir()):
                if not proj_dir.is_dir():
                    continue
                # Skip hidden dirs (e.g. uploads) and the uploads dir itself
                if proj_dir.name.startswith(".") or proj_dir.name == "uploads":
                    continue
                has_svgs = (proj_dir / "svg_output").is_dir() or (proj_dir / "svg_final").is_dir()
                if not has_svgs:
                    continue
                # Deduplicate by basename so Docker-internal paths stored in the DB
                # (/app/projects/foo) match the filesystem entry (foo) regardless of
                # where the volume is mounted on the host.
                if proj_dir.name in known_project_names:
                    continue

                # Count slides
                svg_dir = proj_dir / "svg_final"
                if not svg_dir.is_dir():
                    svg_dir = proj_dir / "svg_output"
                slide_count = len(list(svg_dir.glob("*.svg")))

                # Use directory mtime as created_at
                try:
                    mtime = proj_dir.stat().st_mtime
                    created_at = datetime.utcfromtimestamp(mtime).isoformat()
                except OSError:
                    created_at = datetime.utcnow().isoformat()

                # Derive a human-readable topic from the directory name
                # Strip leading format prefix like "ppt169_" if present
                name = proj_dir.name
                name = re.sub(r'^ppt\d+_', '', name)
                topic = name.replace("_", " ")

                # Use the project directory name as the synthetic job ID so that
                # /jobs/{job_id}/slides/{n} can fall back to it.
                synthetic_id = proj_dir.name

                # Find a matching PPTX in exports/ (name contains the project dir name)
                download_url = f"/jobs/{synthetic_id}/download"

                result.append({
                    "id": synthetic_id,
                    "status": "done",
                    "topic": topic,
                    "project_path": str(proj_dir),
                    "slide_count": slide_count,
                    "created_at": created_at,
                    "updated_at": created_at,
                    "download_url": download_url,
                    "synthetic": True,
                })
                known_project_names.add(proj_dir.name)

        # Sort all results by created_at descending
        result.sort(key=lambda j: j.get("created_at") or "", reverse=True)
        return result

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

    def delete_job(self, job_id: str) -> None:
        """Delete the job row from the DB."""
        with _get_conn() as conn:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.commit()

    def cancel_job(self, job_id: str) -> None:
        """Signal the agent runner to stop on its next iteration."""
        if job_id in _cancel_events:
            _cancel_events[job_id].set()

    def is_cancelled(self, job_id: str) -> bool:
        """Return True if the job has been cancelled."""
        event = _cancel_events.get(job_id)
        return event is not None and event.is_set()

    def cleanup(self, job_id: str) -> None:
        _confirm_events.pop(job_id, None)
        _cancel_events.pop(job_id, None)
        _log_queues.pop(job_id, None)
