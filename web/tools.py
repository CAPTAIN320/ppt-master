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
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path("/app")
SCRIPTS_DIR = REPO_ROOT / "skills" / "ppt-master" / "scripts"

MAX_LIST_DIR_ENTRIES = 120
MAX_READ_FILES = 8
_CONFIRM_UI_ERROR = "confirm_ui files are not used in web mode - call the confirm_gate tool directly."
_MOUNT_ALIASES = ("projects", "skills", "examples")


def slide_subagent_enabled() -> bool:
    """Feature flag: isolated per-slide sub-agent tools (default ON)."""
    return os.environ.get("SLIDE_SUBAGENT", "1").lower() not in ("0", "false", "no")


# Backward-compatible module-level constant evaluated at import time; prefer
# slide_subagent_enabled() for runtime checks.
SLIDE_SUBAGENT = slide_subagent_enabled()


@dataclass
class ToolContext:
    """Per-job context for tools that need to call the LLM (slide sub-agent)."""

    client: Any  # AsyncOpenAI built once in run_job
    model: str
    auto_confirm: bool = False
    slide_model: str | None = None  # SLIDE_SUBAGENT_MODEL override
    slide_semaphore: asyncio.Semaphore | None = None  # per-job cap, created lazily


def _parse_page_count_target(raw: Any) -> int | None:
    """Best-effort parse of a Strategist page_count value into a single
    integer target for the frontend's progress indicator.

    Accepts a plain string/int, or the confirm_ui shape ``{"value": "12-15"}``.
    Uses the **upper bound** when a range is given (e.g. ``"12-15"`` -> 15,
    ``"~20+"`` -> 20) — a bar that fills to <100% and then jumps to done on a
    shorter-than-expected deck is a safer failure mode than one that visibly
    overflows past 100%. Returns None when no integer can be extracted (e.g.
    ``"Auto"``), so the frontend falls back to an indeterminate indicator.
    """
    if isinstance(raw, dict):
        raw = raw.get("value")
    if raw is None:
        return None
    numbers = re.findall(r"\d+", str(raw))
    if not numbers:
        return None
    return max(int(n) for n in numbers)


# ── Tool schemas (OpenAI function-calling format) ─────────────────────────────

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read one file (`path`) or several (`paths`). ALWAYS batch independent reads into one call "
                "with `paths` (max 8). Single `path` returns {content}; `paths` returns {files:[{path,content|error}]}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or repo-relative file path"},
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Read several files in ONE call (preferred). Returns {files:[{path,content|error}]}.",
                    },
                },
                "required": [],
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
            "description": "List files and directories at a given path (capped at 120 entries; use pattern to filter).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path to list"},
                    "pattern": {"type": "string", "description": "Optional case-insensitive substring filter on entry names."},
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


SLIDE_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "generate_slide_svg",
            "description": (
                "Step 6 (Executor) — generate ONE slide SVG in an isolated sub-agent and write it to "
                "<project_path>/svg_output/. The sub-agent reads design_spec.md §IX, spec_lock.md, templates and "
                "images itself, so do NOT read executor-base.md / shared-standards.md / mode / visual-style files "
                "and do NOT write SVG with write_file. It runs the SVG quality check, self-corrects once, writes "
                "speaker notes, and emits slide_ready automatically. Returns a tiny status JSON."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Project dir, e.g. /app/projects/foo_ppt169_20261117"},
                    "slide_number": {"type": "integer", "description": "1-based page number matching '#### Slide NN' (also 'P01 - Slide 01:' / 'Page 1 -' / '第3页：' variants) in design_spec.md §IX"},
                    "filename": {"type": "string", "description": "Optional explicit '<NN>_<slug>.svg'. Default: derived from the §IX page name."},
                    "extra_instructions": {"type": "string", "description": "Optional (<=800 chars): user feedback / fix request for this slide (e.g. regeneration after review)."},
                    "with_notes": {"type": "boolean", "description": "Whether to generate speaker notes (default: true)."},
                    "title": {"type": "string", "description": "Optional fallback title when design_spec.md §IX has no matching '#### Slide NN' (also 'P01 - Slide 01:' / 'Page 1 -' / '第3页：' variants) block."},
                    "slide_spec": {"type": "string", "description": "Optional inline brief for this slide, used only when §IX has no matching block."},
                },
                "required": ["project_path", "slide_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_slides_batch",
            "description": (
                "Generate many slide SVGs via isolated sub-agents with bounded concurrency (preferred over repeated "
                "generate_slide_svg). All requested slides are generated in parallel (bounded by max_concurrency) "
                "using a locked header/footer chrome contract - no serial warm-up. Writes "
                "notes/total.md when all notes exist. Returns a compact summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Project dir, e.g. /app/projects/foo_ppt169_20261117"},
                    "slide_numbers": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "1-based slide numbers to generate. Pass an empty array [] (or omit) to generate ALL slides in design_spec.md §IX.",
                    },
                    "max_concurrency": {"type": "integer", "description": "Maximum parallel slide sub-agents, 1 to 6 (default: 4)."},
                    "overwrite": {"type": "boolean", "description": "Regenerate slides whose SVG already exists (default: false — skip for idempotent resume)."},
                    "extra_instructions": {
                        "type": "object",
                        "description": "Optional map of slide_number (as string, e.g. '3') to extra instructions string.",
                    },
                    "with_notes": {"type": "boolean", "description": "Whether to generate speaker notes (default: true)."},
                },
                "required": ["project_path"],
            },
        },
    },
]


def get_tool_definitions() -> list[dict]:
    """Tool schemas to advertise; slide sub-agent tools only when SLIDE_SUBAGENT is on."""
    if slide_subagent_enabled():
        return TOOL_DEFINITIONS + SLIDE_TOOL_DEFINITIONS
    return list(TOOL_DEFINITIONS)


# ── Dispatcher ────────────────────────────────────────────────────────────────


def _resolve_path(path: str) -> Path:
    s = str(path).strip()
    for m in _MOUNT_ALIASES:  # "/projects", "/projects/x", "/skills/...", "/examples/..."
        if s == f"/{m}" or s.startswith(f"/{m}/"):
            s = str(REPO_ROOT) + s  # -> /app/projects/...
            break
    p = Path(s)
    return p if p.is_absolute() else REPO_ROOT / p


def _is_confirm_ui_path(p: Path) -> bool:
    sp = str(p).replace("\\", "/")
    return "/confirm_ui" in sp or "confirm_ui.md" in sp


_ICON_SYNONYMS: dict[str, list[str]] = {
    "tea": ["cup", "teapot", "mug", "coffee"], "teacup": ["cup", "mug"], "matcha": ["leaf", "cup"],
    "ceremony": ["sparkles", "calendar-event"], "garden": ["plant", "flower", "trees"],
    "temple": ["building-pagoda", "home"], "chart": ["chart-bar", "chart-line"], "graph": ["chart-line"],
    "growth": ["trending-up"], "money": ["coin", "currency-dollar"], "person": ["user"], "people": ["users"],
    "team": ["users"], "idea": ["bulb"], "global": ["world"], "security": ["shield"],
}
_ICON_LIB_ALIASES = {"chunk": "chunk-filled"}


def _resolve_missing_icons(project: Path, lib_names: list[str]) -> tuple[dict[str, str], list[str]]:
    """Fuzzy-resolve icon names missing from the library to the nearest existing icon.

    Copies the match into ``<project>/icons/<lib>/`` under BOTH the resolved and
    the requested filename so ``data-icon="<requested>"`` still resolves at finalize.
    Returns ``(resolved {requested: lib/resolved}, unresolved [requested])``.
    """
    icons_root = REPO_ROOT / "skills" / "ppt-master" / "templates" / "icons"
    resolved: dict[str, str] = {}
    unresolved: list[str] = []
    for raw in lib_names:
        if "/" in raw:
            lib, _, name = raw.partition("/")
        else:
            lib, name = "chunk-filled", raw
        lib = _ICON_LIB_ALIASES.get(lib, lib)
        libdir = icons_root / lib
        dst = project / "icons" / lib
        if not name or not libdir.is_dir() or (libdir / f"{name}.svg").is_file() or (dst / f"{name}.svg").is_file():
            continue  # already satisfied by icon_sync (or nothing to resolve against)
        stems = {f.stem for f in libdir.glob("*.svg")}
        cands = [c for c in _ICON_SYNONYMS.get(name, []) if c in stems]
        cands += sorted((x for x in stems if x == name or x.startswith(name + "-")), key=len)
        cands += sorted((x for x in stems if name in x), key=len)
        tokens = [t for t in name.split("-") if len(t) > 1]
        cands += sorted((x for x in stems if any(t in x for t in tokens)), key=len)
        cands = [c for c in cands if not c.endswith("-off")] or cands
        if not cands:
            unresolved.append(raw)
            continue
        best = cands[0]
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(libdir / f"{best}.svg", dst / f"{best}.svg")
        shutil.copy2(libdir / f"{best}.svg", dst / f"{name}.svg")
        resolved[raw] = f"{lib}/{best}"
    return resolved, unresolved


async def dispatch_tool(
    name: str,
    args: dict[str, Any],
    job_id: str,
    store: Any,  # JobStore — avoid circular import
    auto_confirm: bool = False,
    ctx: ToolContext | None = None,
) -> Any:
    """Execute a tool call and return a JSON-serialisable result."""

    if name == "read_file":
        raw_paths = [x for x in (args.get("paths") or []) if isinstance(x, str) and x.strip()]
        if isinstance(args.get("path"), str) and args["path"].strip():
            raw_paths.insert(0, args["path"])
        if not raw_paths:
            return {"error": "read_file needs `path` or `paths`"}

        def _one(rp: str) -> dict[str, Any]:
            p = _resolve_path(rp)
            if _is_confirm_ui_path(p):
                return {"path": str(p), "error": _CONFIRM_UI_ERROR}
            try:
                return {"path": str(p), "content": p.read_text(encoding="utf-8")}
            except FileNotFoundError:
                return {"path": str(p), "error": f"File not found: {p}"}
            except Exception as exc:
                return {"path": str(p), "error": str(exc)}

        if len(raw_paths) == 1 and not args.get("paths"):  # legacy single-file shape
            r = _one(raw_paths[0])
            return {"content": r["content"], "path": r["path"]} if "content" in r else {"error": r["error"]}
        return {"files": [_one(x) for x in raw_paths[:MAX_READ_FILES]]}

    elif name == "write_file":
        p = _resolve_path(args["path"])
        if _is_confirm_ui_path(p):
            return {"error": _CONFIRM_UI_ERROR}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"], encoding="utf-8")
        # Any write under <REPO_ROOT>/projects/<name>/ tracks that project (except uploads)
        projects_root = REPO_ROOT / "projects"
        try:
            rel = p.relative_to(projects_root)
        except ValueError:
            rel = None
        if rel is not None and len(rel.parts) >= 2 and rel.parts[0] != "uploads":
            store.set_project_path(job_id, str(projects_root / rel.parts[0]))
        # If this looks like an SVG output or project spec, update the project path hint
        elif "svg_output" in str(p) or "svg_final" in str(p):
            project_path = str(p.parent.parent)
            store.set_project_path(job_id, project_path)
        elif p.name in ("design_spec.md", "spec_lock.md"):
            store.set_project_path(job_id, str(p.parent))
        return {"written": str(p), "bytes": len(args["content"].encode())}

    elif name == "list_dir":
        p = _resolve_path(args["path"])
        if _is_confirm_ui_path(p):
            return {"error": _CONFIRM_UI_ERROR}
        if "templates/icons" in str(p).replace("\\", "/"):
            return {
                "error": "list_dir on templates/icons is disabled (5000+ files). Pass your desired icon names directly to "
                "icon_sync.py in ONE batch call, e.g. run_script(script='skills/ppt-master/scripts/icon_sync.py', "
                "args=[<project_path>, 'tabler-outline/chart-bar', 'tabler-outline/cup', ...]). Names that do not exist "
                "are auto-resolved to the nearest icon and reported - no searching needed."
            }
        try:
            entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
        except FileNotFoundError:
            return {"error": f"Directory not found: {p}"}
        except NotADirectoryError:
            return {"error": f"Not a directory: {p}"}
        except Exception as exc:
            return {"error": str(exc)}
        pat = str(args.get("pattern") or "").strip().lower()
        if pat:
            entries = [e for e in entries if pat in e.name.lower()]
        total = len(entries)
        shown = entries[:MAX_LIST_DIR_ENTRIES]
        out: dict[str, Any] = {
            "path": str(p),
            "entries": [{"name": e.name, "type": "file" if e.is_file() else "dir"} for e in shown],
        }
        if total > len(shown):
            out.update(total_entries=total, truncated=True)
            hint = f"Showing first {len(shown)} of {total}. Narrow with list_dir(path, pattern='<substring>')."
            out["hint"] = hint
        return out

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
        returncode = proc.returncode
        output = "".join(output_lines[-200:])  # last 200 lines to avoid huge tool results
        if Path(str(script)).name == "icon_sync.py" and returncode != 0 and "[MISSING]" in output and script_args:
            try:
                resolved, unresolved = _resolve_missing_icons(_resolve_path(str(script_args[0])), [str(a) for a in script_args[1:]])
            except Exception as exc:  # never let the helper break the tool
                resolved, unresolved = {}, []
                output += f"\n[icon auto-resolve skipped: {exc}]"
            if resolved:
                output += (
                    "\n[AUTO-RESOLVED] "
                    + "; ".join(f"{m} -> {r}" for m, r in resolved.items())
                    + "\n(copied under both requested and resolved names - use either in data-icon; no spec edit needed)"
                )
            if unresolved:
                output += "\n[STILL MISSING] " + ", ".join(unresolved)
            if resolved and not unresolved:
                returncode = 0
        return {"returncode": returncode, "output": output}

    elif name == "confirm_gate":
        recommendations = args.get("recommendations", {})

        # Push the Strategist's page_count target as a dedicated progress
        # event *before* branching on auto_confirm, so the frontend's
        # progress indicator learns the slide-generation target regardless
        # of whether the Eight Confirmations UI is actually shown to a human.
        target = _parse_page_count_target(recommendations.get("page_count"))
        await store.push_event(job_id, {"type": "progress_target", "target": target})

        if auto_confirm:
            # Auto-confirm: use the agent's own recommendations as the confirmed result.
            # This bypasses the browser confirmation UI and lets the pipeline proceed immediately.
            store.submit_confirm(job_id, recommendations)
            await store.append_log(job_id, "\n[Confirm gate] Auto-confirmed with agent recommendations.\n")
            return {"confirmed": True, "user_choices": recommendations}
        else:
            # Blocking confirmation: push event to the browser UI and wait for the user.
            await store.push_event(job_id, {"type": "confirm_required", "data": recommendations})
            store.set_status(job_id, "confirm_pending")
            await store.wait_for_confirm(job_id)
            user_choices = store.get_confirm_result(job_id)
            store.set_status(job_id, "running")
            await store.append_log(job_id, "\n[Confirm gate] User confirmation received.\n")
            return {"confirmed": True, "user_choices": user_choices}

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

    elif name in ("generate_slide_svg", "generate_slides_batch"):
        if not slide_subagent_enabled():
            return {"error": f"Unknown tool: {name}"}
        if ctx is None:
            return {"error": "slide sub-agent unavailable (no LLM context)"}
        from . import slide_subagent  # lazy import: avoids import-time cycles

        fn = slide_subagent.generate_slide_svg if name == "generate_slide_svg" else slide_subagent.generate_slides_batch
        return await fn(args, job_id=job_id, store=store, ctx=ctx)

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
