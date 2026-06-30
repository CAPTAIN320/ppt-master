"""
Tool definitions for the PPT Master agent.

Provides:
  TOOL_DEFINITIONS  — list of OpenAI-format tool schemas passed to the LLM
  dispatch_tool()   — async dispatcher that executes a tool call by name
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

REPO_ROOT = Path("/app")
SCRIPTS_DIR = REPO_ROOT / "skills" / "ppt-master" / "scripts"

# ── Tool schemas (OpenAI function-calling format) ─────────────────────────────

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Returns the file text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or repo-relative file path"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write text content to a file, creating parent directories as needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or repo-relative file path"},
                    "content": {"type": "string", "description": "Text content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and directories at a given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path to list"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_script",
            "description": ("Run a Python script from the skills/ppt-master/scripts/ directory " "or any absolute path. Returns stdout/stderr and return code."),
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": ("Script path relative to repo root (e.g. " "'skills/ppt-master/scripts/project_manager.py') " "or absolute path."),
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Command-line arguments to pass to the script",
                    },
                },
                "required": ["script"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_gate",
            "description": (
                "Present the Eight Confirmations to the user and wait for their response. "
                "Call this at Step 4 of the pipeline with the Strategist's recommendations. "
                "This call BLOCKS until the user submits their choices via the Web UI."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "recommendations": {
                        "type": "object",
                        "description": "The Strategist's Eight Confirmations recommendations JSON",
                    },
                },
                "required": ["recommendations"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "slide_ready",
            "description": "Signal that a slide SVG has been written and is ready for preview.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slide_number": {"type": "integer", "description": "1-based slide number"},
                    "svg_path": {"type": "string", "description": "Path to the SVG file"},
                },
                "required": ["slide_number", "svg_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch the text content of a URL (for web source ingestion).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                },
                "required": ["url"],
            },
        },
    },
]


# ── Dispatcher ────────────────────────────────────────────────────────────────


def _resolve_path(path: str) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    return REPO_ROOT / p


async def dispatch_tool(
    name: str,
    args: dict[str, Any],
    job_id: str,
    store: Any,  # JobStore — avoid circular import
) -> Any:
    """Execute a tool call and return a JSON-serialisable result."""

    if name == "read_file":
        p = _resolve_path(args["path"])
        try:
            return {"content": p.read_text(encoding="utf-8"), "path": str(p)}
        except FileNotFoundError:
            return {"error": f"File not found: {p}"}
        except Exception as exc:
            return {"error": str(exc)}

    elif name == "write_file":
        p = _resolve_path(args["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"], encoding="utf-8")
        # If this looks like an SVG output, update the project path hint
        if "svg_output" in str(p) or "svg_final" in str(p):
            project_path = str(p.parent.parent)
            store.set_project_path(job_id, project_path)
        return {"written": str(p), "bytes": len(args["content"].encode())}

    elif name == "list_dir":
        p = _resolve_path(args["path"])
        try:
            entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
            return {
                "path": str(p),
                "entries": [{"name": e.name, "type": "file" if e.is_file() else "dir"} for e in entries],
            }
        except FileNotFoundError:
            return {"error": f"Directory not found: {p}"}

    elif name == "run_script":
        script = args["script"]
        script_args = args.get("args", [])

        # Intercept confirm_ui/server.py — web UI uses confirm_gate tool instead
        if "confirm_ui" in script:
            return {
                "output": "[Web UI] confirm_ui/server.py is not used in web mode. Call the confirm_gate tool directly to trigger the Eight Confirmations UI.",
                "returncode": 0,
            }

        script_path = _resolve_path(script)
        cmd = ["python3", str(script_path)] + [str(a) for a in script_args]
        await store.append_log(job_id, f"\n$ {' '.join(cmd)}\n")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(REPO_ROOT),
            env={**os.environ},
        )
        output_lines = []
        async for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace")
            output_lines.append(line)
            await store.append_log(job_id, line)

        await proc.wait()
        return {
            "returncode": proc.returncode,
            "output": "".join(output_lines[-200:]),  # last 200 lines to avoid huge tool results
        }

    elif name == "confirm_gate":
        recommendations = args.get("recommendations", {})
        store.set_confirm_pending(job_id, recommendations)
        await store.push_event(
            job_id,
            {
                "type": "confirm_required",
                "data": recommendations,
            },
        )
        await store.wait_for_confirm(job_id)
        result = store.get_confirm_result(job_id)
        await store.append_log(job_id, f"\n[Confirm gate passed] User choices: {json.dumps(result)}\n")
        return {"confirmed": True, "user_choices": result}

    elif name == "slide_ready":
        slide_num = args["slide_number"]
        svg_path = args["svg_path"]
        await store.push_event(
            job_id,
            {
                "type": "slide_ready",
                "slide": slide_num,
                "url": f"/jobs/{job_id}/slides/{slide_num}",
                "svg_path": svg_path,
            },
        )
        return {"signalled": True, "slide": slide_num}

    elif name == "web_fetch":
        url = args["url"]
        # Strategy 1: use the existing web_to_md.py script (handles curl_cffi, BS4, etc.)
        web_to_md_script = REPO_ROOT / "skills" / "ppt-master" / "scripts" / "source_to_md" / "web_to_md.py"
        if web_to_md_script.exists():
            try:
                proc = await asyncio.create_subprocess_exec(
                    "python3",
                    str(web_to_md_script),
                    url,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(REPO_ROOT),
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
                if proc.returncode == 0 and stdout:
                    content = stdout.decode(errors="replace")
                    return {"content": content[:60000], "url": url, "method": "web_to_md"}
            except Exception:
                pass  # fall through to requests

        # Strategy 2: requests with browser User-Agent
        try:
            import urllib.request

            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " "AppleWebKit/537.36 (KHTML, like Gecko) " "Chrome/120.0.0.0 Safari/537.36"),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                content = resp.read().decode(errors="replace")
            return {"content": content[:60000], "url": url, "method": "urllib"}
        except Exception as exc:
            return {"error": str(exc), "url": url}

    else:
        return {"error": f"Unknown tool: {name}"}
