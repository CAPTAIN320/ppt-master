# Docker + Web UI Architecture for PPT Master

## Overview

This document describes the architecture for running PPT Master as a Dockerized web service with a browser-based UI. The user submits a topic or uploads source files via the Web UI; the full PPT generation pipeline runs inside the container and produces a downloadable `.pptx`.

## Key Insight: What the "Agent" Actually Is

The current system works because **Zoo Code (Claude) IS the agent** — it reads SKILL.md, writes SVG files directly, calls scripts via tools, and manages the serial pipeline. To dockerize this, we replicate that agent loop programmatically using the OpenAI-compatible API exposed by the LiteLLM proxy (`claude-sonnet-4.6`).

---

## Architecture Diagram

```
Browser
  └── Web UI (SPA)
        • Job form: topic / file upload / model picker
        • Real-time log stream (WebSocket)
        • Eight Confirmations panel
        • SVG slide thumbnails
        • Download PPTX button
        │
        │ HTTP / WebSocket (port 8080)
        ▼
Docker Container
  ├── FastAPI Server (web/server.py)
  │     POST   /jobs
  │     GET    /jobs/:id
  │     WS     /ws/:id          ← real-time log stream
  │     POST   /jobs/:id/confirm
  │     GET    /jobs/:id/slides/:n
  │     GET    /jobs/:id/download
  │
  ├── Agent Runner (web/agent_runner.py)
  │     OpenAI-compatible tool-calling loop
  │     System prompt = SKILL.md
  │     Model = claude-sonnet-4.6 via LiteLLM proxy
  │
  ├── Tool Dispatcher (web/tools.py)
  │     read_file / write_file / list_dir
  │     run_script → subprocess existing Python scripts
  │     confirm_gate → asyncio.Event (blocks until user confirms)
  │     slide_ready → signals UI to render thumbnail
  │
  ├── Job Store (web/job_store.py)
  │     SQLite-backed job state
  │     asyncio.Event per job for confirm gate
  │
  ├── Existing Scripts (skills/ppt-master/scripts/) — UNCHANGED
  │     project_manager.py, source_to_md/*, image_gen.py,
  │     finalize_svg.py, svg_to_pptx.py, …
  │
  └── Volume Mounts
        /app/projects/   ← generated project files
        /app/exports/    ← exported .pptx files

External APIs (via LiteLLM proxy at https://litellm-v2.neopandahq.com/v1)
  • claude-sonnet-4.6  ← agent LLM (writes SVG, orchestrates pipeline)
  • gemini-2.5-flash-image ← image generation (IMAGE_BACKEND=openai)
```

---

## File Structure

```
ppt-master/
├── Dockerfile
├── docker-compose.yml
├── web/
│   ├── __init__.py
│   ├── server.py          FastAPI app (REST + WebSocket)
│   ├── agent_runner.py    LLM tool-calling loop
│   ├── job_store.py       SQLite job state + asyncio confirm gate
│   ├── tools.py           Tool definitions (JSON schema) + dispatcher
│   ├── skill_prompt.py    Loads SKILL.md as system prompt
│   └── static/
│       └── index.html     Single-file SPA
├── skills/ppt-master/     UNCHANGED
├── projects/              Volume mount
└── exports/               Volume mount
```

---

## WebSocket Event Protocol

Messages sent from server → browser over `WS /ws/:id`:

```jsonc
{"type": "log",              "text": "Creating project..."}
{"type": "confirm_required", "data": { /* recommendations.json schema */ }}
{"type": "slide_ready",      "slide": 3, "url": "/jobs/abc/slides/3"}
{"type": "job_done",         "download_url": "/jobs/abc/download"}
{"type": "error",            "message": "..."}
```

---

## Data Flow

1. User submits topic + optional files via Web UI form.
2. `POST /jobs` creates a job record in SQLite, enqueues `run_job()` as an asyncio task.
3. Browser opens `WS /ws/:id` to receive real-time log events.
4. Agent runner calls `claude-sonnet-4.6` with SKILL.md as system prompt.
5. Agent issues tool calls; dispatcher executes them (file I/O, script runs).
6. At Step 4 (Eight Confirmations), agent calls `confirm_gate` tool → server sends `confirm_required` event → browser renders confirmation panel → user submits → `POST /jobs/:id/confirm` → asyncio.Event unblocks agent.
7. Agent writes SVG pages directly as text; `write_file` tool saves them; `slide_ready` events stream thumbnails to browser.
8. Agent calls `run_script(finalize_svg.py)` then `run_script(svg_to_pptx.py)`.
9. Server sends `job_done` event with download URL.
10. User clicks Download → `GET /jobs/:id/download` → `.pptx` file streamed.

---

## Environment Variables

All passed via `.env` / `docker-compose.yml`:

| Variable          | Purpose                                                  |
| ----------------- | -------------------------------------------------------- |
| `OPENAI_API_KEY`  | LiteLLM proxy auth key (used for both agent + image gen) |
| `OPENAI_BASE_URL` | LiteLLM proxy base URL                                   |
| `AGENT_MODEL`     | Model name for agent (default: `claude-sonnet-4.6`)      |
| `AGENT_BASE_URL`  | Agent LLM endpoint (defaults to `OPENAI_BASE_URL`)       |
| `IMAGE_BACKEND`   | Image generation backend (default: `openai`)             |
| `OPENAI_MODEL`    | Image generation model (e.g. `gemini-2.5-flash-image`)   |

---

## Implementation Notes

### SVG Generation

SKILL.md rule 9 mandates SVG is hand-written by the agent, not script-generated. The LLM writes raw `<svg>...</svg>` XML as text output; the agent runner captures it via the `write_file` tool and saves it to disk.

### Context Window

`claude-sonnet-4.6` has 1M token context via the `anthropic-beta: context-1m-2025-08-07` header configured in the LiteLLM proxy. Pass this header via `AsyncOpenAI(default_headers={...})`.

### Eight Confirmations Gate

The only blocking point in the pipeline. Implemented as an `asyncio.Event` per job. Agent runner `await`s the event; `POST /jobs/:id/confirm` sets it.

### Existing Scripts

Zero changes to any file under `skills/ppt-master/`. The agent runner calls them via `subprocess` exactly as Zoo Code does today.

---

## Running Locally

```bash
# Build and start
docker compose up --build

# Open browser
open http://localhost:8080

# View logs
docker compose logs -f

# Stop
docker compose down
```
