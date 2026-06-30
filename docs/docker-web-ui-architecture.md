# Docker + Web UI Architecture for PPT Master

## Overview

This document describes the architecture for running PPT Master as a Dockerized web service with a browser-based UI. The user submits a topic or uploads source files via the Web UI; the full PPT generation pipeline runs inside the container and produces a downloadable `.pptx`.

## Key Insight: What the "Agent" Actually Is

The current system works because **Zoo Code (Claude) IS the agent** — it reads SKILL.md, writes SVG files directly, calls scripts via tools, and manages the serial pipeline. To dockerize this, we replicate that agent loop programmatically using the OpenAI-compatible API (pointing at any LiteLLM proxy or direct provider endpoint).

---

## Architecture Diagram

```
Browser
  └── Web UI (SPA — web/static/index.html)
        • Left panel: topic textarea, file upload, canvas format, model picker
        • Right panel tabs: Log | Confirm | Slides
        • Real-time log stream (WebSocket)
        • Eight Confirmations panel (Confirm tab, top + bottom submit buttons)
        • SVG slide thumbnail gallery (Slides tab, lightbox on click)
        • Download PPTX bar (appears on job_done)
        │
        │ HTTP / WebSocket (port 8080)
        ▼
Docker Container
  ├── FastAPI Server (web/server.py)
  │     GET    /                          Serve SPA (index.html)
  │     POST   /jobs                      Create job (multipart/form-data)
  │     GET    /jobs/{id}                 Job status + metadata (no log)
  │     WS     /ws/{id}                   Real-time event stream
  │     POST   /jobs/{id}/confirm         Submit Eight Confirmations result
  │     GET    /jobs/{id}/slides/{n}      Serve SVG for slide N (1-based)
  │     GET    /jobs/{id}/download        On-demand export: runs svg_to_pptx.py, streams .pptx
  │     GET    /debug/test-llm            LLM connectivity smoke test
  │     GET    /static/{path}             Static file serving
  │
  ├── Agent Runner (web/agent_runner.py)
  │     AsyncOpenAI tool-calling loop (streaming)
  │     System prompt = short web-mode preamble (skill_prompt.py)
  │     SKILL.md injected in the initial user message
  │     Model = configurable via AGENT_MODEL env var
  │     default_headers={"User-Agent": "ppt-master/1.0"}  ← Cloudflare WAF fix
  │     max_iterations = 200
  │
  ├── Tool Dispatcher (web/tools.py)
  │     read_file    → read any file on disk
  │     write_file   → write file, auto-detect svg_output/ to set project_path
  │     list_dir     → list directory entries
  │     run_script   → subprocess existing Python scripts (stdout streamed to log)
  │                    confirm_ui/server.py calls are intercepted and no-op'd
  │     confirm_gate → set confirm_pending, push confirm_required event,
  │                    await asyncio.Event until user submits
  │     slide_ready  → push slide_ready event to WebSocket queue
  │     web_fetch    → fetch URL via web_to_md.py or urllib fallback
  │
  ├── Job Store (web/job_store.py)
  │     SQLite at /app/data/.job_store.db
  │     Per-job asyncio.Event for confirm gate
  │     Per-job asyncio.Queue for WebSocket event streaming
  │
  ├── System Prompt Builder (web/skill_prompt.py)
  │     Short neutral preamble (avoids proxy content filters)
  │     Web-mode instruction: use confirm_gate, not confirm_ui/server.py
  │     SKILL.md injected as first user message (not system prompt)
  │
  ├── Existing Scripts (skills/ppt-master/scripts/) — UNCHANGED
  │     project_manager.py, source_to_md/*, image_gen.py,
  │     finalize_svg.py, svg_to_pptx.py, …
  │
  └── Volume Mounts
        /app/projects/          ← generated project files (PPTX lives here too)
        /app/projects/uploads/  ← uploaded source files (per job_id subdirectory)
        /app/data/              ← SQLite job store (isolated from project files)

External APIs (configured via .env)
  • Agent LLM  — AGENT_BASE_URL / AGENT_API_KEY  (e.g. LiteLLM proxy or direct)
  • Image gen  — OPENAI_BASE_URL / OPENAI_API_KEY (e.g. gemini-2.5-flash-image)
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
│   ├── agent_runner.py    LLM tool-calling loop (streaming, max 200 iterations)
│   ├── job_store.py       SQLite job state + asyncio confirm gate + event queue
│   ├── tools.py           Tool schemas (OpenAI format) + async dispatcher
│   ├── skill_prompt.py    Short system prompt + SKILL.md loader
│   └── static/
│       └── index.html     Single-file SPA (dark/light theme, 3 tabs)
├── skills/ppt-master/     UNCHANGED
├── projects/              Volume mount (generated project files; PPTX exported here)
└── data/                  Volume mount (SQLite job store)
```

---

## API Endpoints

### `POST /jobs`

Creates a new generation job. Accepts `multipart/form-data`:

| Field           | Type            | Default             | Description                                  |
| --------------- | --------------- | ------------------- | -------------------------------------------- |
| `topic`         | `string` (form) | required            | Presentation topic / description             |
| `canvas_format` | `string` (form) | `ppt169`            | Canvas format (`ppt169`, `ppt43`, `ppt1610`) |
| `model`         | `string` (form) | `claude-sonnet-4.6` | LLM model name                               |
| `files`         | `UploadFile[]`  | `[]`                | Optional source files                        |

Uploaded files are saved to `/app/projects/uploads/{job_id}/{filename}`.

Returns `{"job_id": "...", "status": "queued"}` and immediately launches the agent as an asyncio background task.

### `GET /jobs/{job_id}`

Returns job metadata (status, project_path, error_message, timestamps). The `log` field is excluded to avoid huge payloads.

### `WS /ws/{job_id}`

Real-time event stream. On connect, replays the accumulated log as a single `log` event (catch-up for reconnects), then streams new events from the per-job asyncio queue. Closes when a `job_done` or `error` event is sent, or when the job reaches a terminal state during a 30-second queue timeout.

### `POST /jobs/{job_id}/confirm`

Accepts a JSON body (the user's Eight Confirmations choices). Requires `status == confirm_pending`; returns `409` otherwise. Calls `store.submit_confirm()` which writes the result to SQLite and sets the per-job `asyncio.Event`, unblocking the agent runner.

### `GET /jobs/{job_id}/slides/{n}`

Serves the Nth SVG file (1-based, sorted alphabetically) from the project's `svg_output/` directory, falling back to `svg_final/` if `svg_output/` does not exist.

### `GET /jobs/{job_id}/download`

On-demand PPTX export. Looks up `project_path` from the DB (or falls back to treating `job_id` as a project directory name for orphan entries), then runs `svg_to_pptx.py <project_path>` synchronously via `subprocess.run`. Once the script exits successfully, the most recently modified `.pptx` inside the project directory is streamed as a file download. Returns `404` if the project directory is not found, `500` if the script fails or produces no `.pptx`.

### `GET /debug/test-llm`

Smoke-tests the LLM connection with three minimal requests (no tools, with tools, with system prompt) and returns a JSON report. Useful for diagnosing proxy or API key issues without running a full job.

---

## WebSocket Event Protocol

Messages sent **server → browser** over `WS /ws/{job_id}`:

```jsonc
// Incremental agent output / script stdout
{"type": "log", "text": "..."}

// Eight Confirmations gate — agent is blocked, waiting for user
{"type": "confirm_required", "data": { /* Strategist recommendations object */ }}

// SVG slide written and ready for preview
{"type": "slide_ready", "slide": 3, "url": "/jobs/abc/slides/3", "svg_path": "/app/projects/..."}

// Pipeline complete — download_url points to the on-demand export endpoint
{"type": "job_done", "download_url": "/jobs/abc/download", "filename": "deck.pptx"}

// Unrecoverable error
{"type": "error", "message": "..."}
```

The browser reconnects automatically (2-second delay) if the WebSocket closes while the job is still `running` or `queued`.

---

## Agent Loop (`agent_runner.py`)

```
run_job()
  │
  ├─ Build AsyncOpenAI client
  │    api_key  = AGENT_API_KEY  || OPENAI_API_KEY
  │    base_url = AGENT_BASE_URL || OPENAI_BASE_URL
  │    default_headers = {"User-Agent": "ppt-master/1.0"}   ← Cloudflare WAF fix
  │
  ├─ system_prompt = get_system_prompt()   (short web-mode preamble)
  ├─ user_message  = topic + canvas_format + uploaded file paths + SKILL.md instructions
  │
  └─ Loop (max 200 iterations):
       │
       ├─ client.chat.completions.create(stream=True, tools=TOOL_DEFINITIONS, max_tokens=16000)
       │    • Text chunks → append_log() → WebSocket queue
       │    • Tool call fragments → assembled into tool_calls_list
       │
       ├─ If finish_reason == "stop" and no tool calls → pipeline complete, break
       │
       └─ For each tool call:
            dispatch_tool(name, args, job_id, store)
            → append tool result to messages as role="tool"

  After loop:
    push job_done event with download_url = /jobs/{job_id}/download
    (PPTX is generated on demand when the user clicks Download)
```

The system prompt is intentionally short and neutral to avoid proxy content filters. The full SKILL.md workflow is injected as part of the initial user message so the agent has complete pipeline instructions without triggering filter heuristics on the system role.

---

## Tool System (`tools.py`)

### `read_file`

Reads any file on disk. Paths may be absolute or relative to `/app` (the repo root).

### `write_file`

Writes text content to a file, creating parent directories as needed. If the path contains `svg_output` or `svg_final`, the tool automatically calls `store.set_project_path()` so the server knows where to find SVG files for the `/slides/{n}` endpoint.

### `list_dir`

Lists directory entries sorted directories-first, then files alphabetically.

### `run_script`

Runs a Python script as a subprocess (`python3 <script> [args...]`), with `cwd=/app`. Stdout and stderr are merged and streamed line-by-line to the job log via `append_log()`. Returns `{"returncode": N, "output": "<last 200 lines>"}`.

**`confirm_ui` interception guard**: if the script path contains `confirm_ui`, the call is intercepted and returns immediately with a no-op message. This prevents the agent from launching the standalone `confirm_ui/server.py` subprocess (which would block indefinitely in web mode) and redirects it to use `confirm_gate` instead.

### `confirm_gate`

The Eight Confirmations blocking point:

1. Calls `store.set_confirm_pending(job_id, recommendations)` — sets `status = confirm_pending` in SQLite and stores the recommendations JSON.
2. Pushes a `confirm_required` WebSocket event containing the recommendations.
3. `await store.wait_for_confirm(job_id)` — suspends the agent coroutine on a per-job `asyncio.Event`.
4. When `POST /jobs/{id}/confirm` arrives, `store.submit_confirm()` writes the user's choices to SQLite, resets `status = running`, and sets the event.
5. The agent resumes, reads `store.get_confirm_result()`, and returns `{"confirmed": true, "user_choices": {...}}` to the LLM.

### `slide_ready`

Pushes a `slide_ready` WebSocket event with the slide number and URL. The browser adds a thumbnail card to the Slides tab immediately.

### `web_fetch`

Fetches a URL for web source ingestion. Strategy 1: delegates to `source_to_md/web_to_md.py` (handles curl_cffi, BeautifulSoup, etc.) with a 60-second timeout. Strategy 2 fallback: `urllib.request` with a browser User-Agent header. Returns up to 60,000 characters of content.

---

## Job State Machine (`job_store.py`)

```
queued
  │  (agent runner starts)
  ▼
running
  │  (agent calls confirm_gate tool)
  ▼
confirm_pending
  │  (POST /jobs/{id}/confirm received)
  ▼
running
  │  (agent completes pipeline)
  ▼
done
  │
  └── (on any LLM or tool exception)
      ▼
    error
```

**Storage**: SQLite at `/app/data/.job_store.db`. Schema:

| Column           | Type | Description                         |
| ---------------- | ---- | ----------------------------------- |
| `id`             | TEXT | UUID job identifier                 |
| `status`         | TEXT | State machine value (see above)     |
| `project_path`   | TEXT | Path to generated project directory |
| `log`            | TEXT | Accumulated text log (append-only)  |
| `confirm_data`   | TEXT | JSON recommendations from agent     |
| `confirm_result` | TEXT | JSON choices submitted by user      |
| `error_message`  | TEXT | Set on failure                      |
| `created_at`     | TEXT | ISO 8601 UTC timestamp              |
| `updated_at`     | TEXT | ISO 8601 UTC timestamp              |

**In-memory structures** (module-level, lost on restart):

- `_confirm_events: dict[str, asyncio.Event]` — one event per job for the confirm gate.
- `_log_queues: dict[str, asyncio.Queue]` — one queue per job for WebSocket streaming.

---

## Eight Confirmations / Confirm Gate

The confirm gate is the only blocking point in the pipeline. Full flow:

1. Strategist finishes its analysis and the agent calls `confirm_gate(recommendations={...})`.
2. `tools.py` sets `status = confirm_pending` and pushes `confirm_required` to the WebSocket queue.
3. Browser receives `confirm_required`, switches to the **Confirm tab**, and renders each field from the recommendations JSON as an editable `<input>` or `<textarea>`.
4. The Confirm tab has **both a top and a bottom "Confirm & Continue" button** (plus a Reset button) so the user does not need to scroll to submit.
5. User edits any fields and clicks **Confirm & Continue** → browser `POST /jobs/{id}/confirm` with the collected JSON.
6. Server validates `status == confirm_pending`, calls `store.submit_confirm()`, returns `{"ok": true}`.
7. `asyncio.Event.set()` unblocks the agent coroutine; agent reads the user choices and continues with SVG generation.
8. Browser hides the Confirm tab and switches back to the Log tab.

---

## Frontend SPA (`web/static/index.html`)

Single HTML file, no build step, no external JS dependencies.

### Layout

Two-column grid (360 px left panel + flexible right panel):

- **Left panel**: topic textarea, drag-and-drop file upload (PDF/DOCX/MD/TXT/PPTX/XLSX), canvas format selector, model selector, Generate button, current job info box.
- **Right panel**: tab bar + tab content area + download bar (shown on completion).

### Tabs

| Tab         | Shown when                        | Content                                                   |
| ----------- | --------------------------------- | --------------------------------------------------------- |
| **Log**     | Always                            | Monospace streaming log with syntax-colored prefixes      |
| **Confirm** | `confirm_required` event received | Eight Confirmations form with top + bottom submit buttons |
| **Slides**  | First `slide_ready` event         | SVG thumbnail grid; click to open lightbox                |

### WebSocket Client

- Connects to `ws[s]://{host}/ws/{job_id}` immediately after job creation.
- Handles all five event types: `log`, `confirm_required`, `slide_ready`, `job_done`, `error`.
- Auto-reconnects with a 2-second delay if the connection drops while the job is still active.

### Log Colorization

Log lines are colorized by prefix pattern (no server-side markup):

| Pattern                              | Color       |
| ------------------------------------ | ----------- |
| `[Agent]`                            | Accent blue |
| `[Tool]`                             | Yellow      |
| `[Tool result]`                      | Muted       |
| `$ ` (shell command)                 | Green       |
| Contains `Error` / `❌`              | Red         |
| Contains `✅` / `complete` / `ready` | Green       |

### Theme

Dark by default; toggleable to light via a header button. Preference persisted in `localStorage`.

---

## File Storage

| Path                              | Contents                                                              |
| --------------------------------- | --------------------------------------------------------------------- |
| `/app/projects/`                  | Generated project directories (one per job); PPTX exported here too  |
| `/app/projects/uploads/{job_id}/` | Source files uploaded by the user for that job                        |
| `/app/data/.job_store.db`         | SQLite job state database                                             |

`/app/projects/` and `/app/data/` are bind-mounted from the host via `docker-compose.yml` so data persists across container restarts. The DB is kept in a separate `/app/data/` volume so that wiping `./projects/` on the host does not destroy job history, and so the volume mount order cannot shadow the database file before `_init_db()` runs.

There is no global `/app/exports/` directory. The `.pptx` file produced by `svg_to_pptx.py` lives inside the project directory (typically `<project_path>/exports/<name>.pptx`) and is served on demand when the user clicks **Download PPTX**.

---

## Environment Variables

All passed via `.env` / `docker-compose.yml`:

| Variable           | Purpose                                                               |
| ------------------ | --------------------------------------------------------------------- |
| `AGENT_API_KEY`    | API key for the agent LLM endpoint (falls back to `OPENAI_API_KEY`)   |
| `AGENT_BASE_URL`   | Base URL for the agent LLM endpoint (falls back to `OPENAI_BASE_URL`) |
| `AGENT_MODEL`      | Model name for the agent (default: `claude-sonnet-4.6`)               |
| `OPENAI_API_KEY`   | API key for image generation (and agent fallback)                     |
| `OPENAI_BASE_URL`  | Base URL for image generation (and agent fallback)                    |
| `IMAGE_BACKEND`    | Image generation backend (default: `openai`)                          |
| `OPENAI_MODEL`     | Image generation model (e.g. `gemini-2.5-flash-image`)                |
| `PYTHONUNBUFFERED` | Set to `1` in `docker-compose.yml` for real-time log streaming        |

`AGENT_BASE_URL` and `AGENT_API_KEY` allow the agent LLM to use a different endpoint from image generation — for example, pointing the agent at a local LiteLLM proxy via `host.docker.internal` while image generation uses a public proxy URL.

`docker-compose.yml` sets `extra_hosts: host.docker.internal:host-gateway` so the container can reach the Mac host on all platforms.

---

## Docker Setup

### Base Image

`python:3.11-slim`

### System Dependencies (installed via `apt-get`)

| Package                                    | Purpose                     |
| ------------------------------------------ | --------------------------- |
| `libcairo2`, `libcairo2-dev`, `pkg-config` | Cairo for SVG rasterization |
| `libpango-1.0-0`, `libpangocairo-1.0-0`    | Pango for text layout       |
| `fonts-noto`, `fonts-noto-cjk`             | CJK font support            |
| `pandoc`                                   | Document conversion         |
| `curl`, `git`                              | General utilities           |

### Python Dependencies

Layer 1 (from `skills/ppt-master/requirements.txt`): existing pipeline deps.

Layer 2 (web server):

```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
openai>=1.30.0
anthropic>=0.40.0
aiofiles>=23.0.0
python-multipart>=0.0.9
```

### Startup Command

```
uvicorn web.server:app --host 0.0.0.0 --port 8080 --log-level info
```

---

## Known Issues Fixed

### SQLite `no such table: jobs` on First Request

**Problem**: `_init_db()` is called at module import time (when Python loads `job_store.py`). In Docker, the `./projects:/app/projects` bind-mount is applied _after_ the Python process starts, so the DB file created by `_init_db()` inside the container layer is immediately shadowed by the empty host directory. All subsequent queries fail with `sqlite3.OperationalError: no such table: jobs`.

**Fix**: Moved `DB_PATH` from `/app/projects/.job_store.db` to `/app/data/.job_store.db`. A dedicated `./data:/app/data` bind-mount is added in `docker-compose.yml` and `mkdir -p /app/data` is added to the `Dockerfile`. Because `/app/data/` is a separate volume from `/app/projects/`, the mount order cannot shadow the database, and wiping `./projects/` on the host no longer destroys job history.

### Cloudflare WAF User-Agent Block

**Problem**: The default `openai` Python SDK sends `User-Agent: OpenAI/Python ...`. Some LiteLLM proxy deployments sit behind Cloudflare, which blocks this UA with a 403.

**Fix**: `AsyncOpenAI` is constructed with `default_headers={"User-Agent": "ppt-master/1.0"}` in `agent_runner.py`, overriding the SDK default for all requests made by the agent.

### `confirm_ui/server.py` Subprocess Delay

**Problem**: SKILL.md Step 4 instructs the agent to run `confirm_ui/server.py` as a subprocess to launch the Eight Confirmations interactive UI. In web mode this subprocess would either block indefinitely or fail silently, stalling the pipeline.

**Fix (two layers)**:

1. **System prompt preamble** (`skill_prompt.py`): explicitly tells the agent "Do NOT call `run_script` on `confirm_ui/server.py`. At Step 4, call the `confirm_gate` tool directly."
2. **Interception guard** (`tools.py` `run_script`): if the script path contains `confirm_ui`, the call is intercepted and returns a no-op success response immediately, preventing any subprocess from being launched even if the agent ignores the system prompt instruction.

---

## Data Flow

1. User fills in topic + optional files and clicks **Generate Presentation**.
2. Browser `POST /jobs` (multipart) → server saves uploaded files to `/app/projects/uploads/{job_id}/`, creates SQLite record, launches `run_job()` as an asyncio task, returns `{"job_id": "..."}`.
3. Browser opens `WS /ws/{job_id}`; server replays existing log then streams new events.
4. Agent runner calls the LLM with the system prompt + user message (which includes SKILL.md instructions). LLM text streams to the Log tab in real time.
5. Agent issues tool calls; dispatcher executes them (file I/O, script subprocesses, web fetches).
6. At Step 4 (Eight Confirmations), agent calls `confirm_gate` → server sends `confirm_required` event → browser switches to Confirm tab → user reviews/edits fields → clicks **Confirm & Continue** → `POST /jobs/{id}/confirm` → `asyncio.Event` unblocks agent.
7. Agent writes SVG pages via `write_file`; after each page calls `slide_ready` → `slide_ready` event → browser adds thumbnail to Slides tab.
8. Agent calls `run_script(finalize_svg.py)` then `run_script(svg_to_pptx.py)`.
9. Agent loop ends (no more tool calls); runner pushes `job_done` event with `download_url = /jobs/{job_id}/download`.
10. Browser shows the download bar; user clicks **Download PPTX** → `GET /jobs/{id}/download` → server runs `svg_to_pptx.py` on demand, finds the `.pptx` inside the project directory, streams it.

---

## Running Locally

```bash
# Copy and fill in API keys
cp .env.example .env
# edit .env: set AGENT_BASE_URL, AGENT_API_KEY (or OPENAI_BASE_URL, OPENAI_API_KEY)

# Build and start
docker compose up --build

# Open browser
open http://localhost:8080

# View logs
docker compose logs -f

# Stop
docker compose down
```
