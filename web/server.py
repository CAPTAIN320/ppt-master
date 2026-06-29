"""
PPT Master Web Server — FastAPI REST + WebSocket.

Endpoints:
  GET  /                          Serve the SPA (web/static/index.html)
  POST /jobs                      Create a new generation job
  GET  /jobs/{job_id}             Get job status + metadata
  WS   /ws/{job_id}               Real-time log + event stream
  POST /jobs/{job_id}/confirm     Submit Eight Confirmations result
  GET  /jobs/{job_id}/slides/{n}  Serve SVG thumbnail for slide N
  GET  /jobs/{job_id}/download    Download the exported .pptx
  GET  /static/{path}             Serve static files
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import re
import shutil
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .agent_runner import run_job
from .job_store import JobStore

app = FastAPI(title="PPT Master", version="1.0.0")

REPO_ROOT = Path("/app")
STATIC_DIR = Path(__file__).parent / "static"
EXPORTS_DIR = REPO_ROOT / "exports"
UPLOADS_DIR = REPO_ROOT / "projects" / "uploads"

EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

store = JobStore()

# Serve static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── SPA root ──────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def serve_spa():
    index = STATIC_DIR / "index.html"
    return HTMLResponse(content=index.read_text(encoding="utf-8"))


# ── Job creation ──────────────────────────────────────────────────────────────


@app.post("/jobs")
async def create_job(
    topic: str = Form(...),
    canvas_format: str = Form(default="ppt169"),
    model: str = Form(default="claude-sonnet-4.6"),
    files: list[UploadFile] = File(default=[]),
):
    """Create a new PPT generation job."""
    job_id = store.create_job()

    # Save uploaded files
    uploaded_files = []
    for upload in files:
        if upload.filename:
            dest = UPLOADS_DIR / job_id / upload.filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(dest, "wb") as f:
                content = await upload.read()
                await f.write(content)
            uploaded_files.append(
                {
                    "path": str(dest),
                    "type": upload.content_type or "application/octet-stream",
                    "name": upload.filename,
                }
            )

    # Launch agent as background task
    asyncio.create_task(
        run_job(
            job_id=job_id,
            topic=topic,
            canvas_format=canvas_format,
            model=model,
            uploaded_files=uploaded_files,
            store=store,
        )
    )

    return JSONResponse({"job_id": job_id, "status": "queued"})


# ── Job status ────────────────────────────────────────────────────────────────


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    # Don't return the full log in the status endpoint (can be huge)
    job_summary = {k: v for k, v in job.items() if k != "log"}
    return JSONResponse(job_summary)


# ── WebSocket log stream ──────────────────────────────────────────────────────


@app.websocket("/ws/{job_id}")
async def websocket_endpoint(websocket: WebSocket, job_id: str):
    await websocket.accept()
    job = store.get_job(job_id)
    if job is None:
        await websocket.send_text(json.dumps({"type": "error", "message": "Job not found"}))
        await websocket.close()
        return

    # Send existing log first (catch-up for reconnects)
    if job["log"]:
        await websocket.send_text(json.dumps({"type": "log", "text": job["log"]}))

    # Stream new events
    try:
        while True:
            event = await store.get_next_event(job_id)
            if event is None:
                # Timeout — check if job is done
                current = store.get_job(job_id)
                if current and current["status"] in ("done", "error"):
                    break
                continue
            await websocket.send_text(json.dumps(event))
            if event.get("type") in ("job_done", "error"):
                break
    except WebSocketDisconnect:
        pass
    finally:
        await websocket.close()


# ── Eight Confirmations submit ────────────────────────────────────────────────


@app.post("/jobs/{job_id}/confirm")
async def submit_confirm(job_id: str, body: dict):
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "confirm_pending":
        raise HTTPException(status_code=409, detail=f"Job is not awaiting confirmation (status: {job['status']})")
    store.submit_confirm(job_id, body)
    return JSONResponse({"ok": True})


# ── SVG slide thumbnail ───────────────────────────────────────────────────────


_MIME_BY_EXT: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}

# Matches href="..." or xlink:href="..." where the value is a local path
_HREF_RE = re.compile(
    r'((?:xlink:)?href=")([^"]+)(")',
    re.IGNORECASE,
)


def _inline_svg_images(svg_text: str, svg_dir: Path) -> str:
    """Replace local file href/xlink:href references with base64 data URIs."""

    def _replace(m: re.Match) -> str:
        prefix, path_val, suffix = m.group(1), m.group(2), m.group(3)
        # Skip already-inlined or remote references
        if path_val.startswith(("data:", "http://", "https://", "#")):
            return m.group(0)
        # Resolve relative to the SVG's own directory
        resolved = (svg_dir / path_val).resolve()
        if not resolved.exists():
            return m.group(0)
        ext = resolved.suffix.lower()
        mime = _MIME_BY_EXT.get(ext) or (mimetypes.guess_type(str(resolved))[0] or "application/octet-stream")
        encoded = base64.b64encode(resolved.read_bytes()).decode("ascii")
        return f'{prefix}data:{mime};base64,{encoded}{suffix}'

    return _HREF_RE.sub(_replace, svg_text)


@app.get("/jobs/{job_id}/slides/{slide_number}")
async def get_slide(job_id: str, slide_number: int):
    """Serve the SVG file for a given slide number, with images inlined as data URIs."""
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    project_path = job.get("project_path")
    if not project_path:
        raise HTTPException(status_code=404, detail="Project path not set yet")

    svg_dir = Path(project_path) / "svg_output"
    if not svg_dir.exists():
        svg_dir = Path(project_path) / "svg_final"

    # Find the Nth SVG (sorted alphabetically)
    svgs = sorted(svg_dir.glob("*.svg"))
    if slide_number < 1 or slide_number > len(svgs):
        raise HTTPException(status_code=404, detail=f"Slide {slide_number} not found")

    svg_file = svgs[slide_number - 1]
    svg_text = svg_file.read_text(encoding="utf-8")
    svg_text = _inline_svg_images(svg_text, svg_file.parent)
    return Response(content=svg_text, media_type="image/svg+xml")


# ── Debug endpoint ────────────────────────────────────────────────────────────


@app.get("/debug/test-llm")
async def debug_test_llm():
    """
    Test the LLM connection with a minimal request (no tools, no system prompt).
    Returns the raw response or error for debugging.
    """
    import traceback
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("AGENT_BASE_URL", os.environ.get("OPENAI_BASE_URL", "")),
    )
    model = os.environ.get("AGENT_MODEL", "claude-sonnet-4.6")

    results = {}

    # Test 1: Minimal call — no tools, no system, just one user message
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Say hello in one word."}],
            max_tokens=10,
        )
        results["test1_no_tools"] = {
            "status": "ok",
            "content": resp.choices[0].message.content,
        }
    except Exception as exc:
        results["test1_no_tools"] = {
            "status": "error",
            "error": str(exc),
            "type": type(exc).__name__,
            "traceback": traceback.format_exc()[-500:],
        }

    # Test 2: With tools
    try:
        resp2 = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Say hello in one word."}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "say_hello",
                        "description": "Say hello",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    },
                }
            ],
            tool_choice="none",
            max_tokens=10,
        )
        results["test2_with_tools"] = {
            "status": "ok",
            "content": resp2.choices[0].message.content,
        }
    except Exception as exc:
        results["test2_with_tools"] = {
            "status": "error",
            "error": str(exc),
            "type": type(exc).__name__,
        }

    # Test 3: With system prompt
    try:
        resp3 = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say hello in one word."},
            ],
            max_tokens=10,
        )
        results["test3_with_system"] = {
            "status": "ok",
            "content": resp3.choices[0].message.content,
        }
    except Exception as exc:
        results["test3_with_system"] = {
            "status": "error",
            "error": str(exc),
            "type": type(exc).__name__,
        }

    # Config info (mask key)
    api_key = os.environ.get("OPENAI_API_KEY", "")
    results["config"] = {
        "model": model,
        "base_url": os.environ.get("AGENT_BASE_URL", os.environ.get("OPENAI_BASE_URL", "")),
        "api_key_prefix": api_key[:8] + "..." if len(api_key) > 8 else "(empty)",
        "api_key_length": len(api_key),
    }

    return JSONResponse(results)


# ── PPTX download ─────────────────────────────────────────────────────────────


@app.get("/jobs/{job_id}/download")
async def download_pptx(job_id: str):
    pptx_path = EXPORTS_DIR / f"{job_id}.pptx"
    if not pptx_path.exists():
        # Fallback: find any pptx in exports
        candidates = sorted(EXPORTS_DIR.glob("*.pptx"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            raise HTTPException(status_code=404, detail="PPTX not found")
        pptx_path = candidates[0]

    return FileResponse(
        str(pptx_path),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=pptx_path.name,
    )
