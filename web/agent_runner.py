"""
Agent runner — drives the PPT Master pipeline via LLM tool-calling.

Uses the OpenAI-compatible API pointing to the LiteLLM proxy.

The agent reads SKILL.md (+ web-mode preamble) as its system prompt and
executes the full serial pipeline:
  project init → source ingestion → Strategist → Eight Confirmations →
  SVG generation → post-processing → PPTX export
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import time
import traceback
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from .job_store import JobStore
from .llm_utils import (  # noqa: F401  (re-exported for backward compatibility)
    _is_gemini_model,
    _is_token_limit_error,
    _max_tokens_for,
    _strip_thinking_content_blocks,
)
from .skill_prompt import get_skill_md, get_system_prompt
from .tools import TOOL_DEFINITIONS, ToolContext, dispatch_tool, get_tool_definitions, slide_subagent_enabled

REPO_ROOT = Path("/app")
PROJECTS_DIR = REPO_ROOT / "projects"

# Number of consecutive non-productive turns (truncation, no-progress-stop, or
# any other unexpected finish_reason) tolerated before the job is failed with
# an actionable error instead of spinning to max_iterations.
STALL_THRESHOLD = 3

_BRAND_PATHS: dict[str, str] = {
    "rakuten-crimson": "skills/ppt-master/templates/brands/rakuten_crimson/",
    "rakuten-mobile":  "skills/ppt-master/templates/brands/rakuten_mobile/",
}

# ── Context-history compaction ────────────────────────────────────────────────

_REF_MARKERS = (
    "skills/ppt-master/references/",
    "skills/ppt-master/workflows/",
    "skills/ppt-master/templates/",
)
_COMPACT_MIN_LEN = 3000
_COMPACTED_PREFIX = "[compacted"


def _history_chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            total += sum(len(b.get("text", "")) for b in c if isinstance(b, dict))
        for tc in m.get("tool_calls") or []:
            total += len(tc.get("function", {}).get("arguments", "") or "")
    return total


def _assert_pairing(messages: list[dict[str, Any]]) -> None:
    """Debug/self-test helper: every tool_call has exactly one tool answer and vice versa."""
    pending: dict[str, int] = {}
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                assert tc["id"] not in pending, f"duplicate tool_call id {tc['id']}"
                pending[tc["id"]] = 0
        elif m.get("role") == "tool":
            tid = m.get("tool_call_id")
            assert tid in pending, f"tool message {tid} has no preceding tool_call"
            pending[tid] += 1
            assert isinstance(m.get("content"), str), "tool content must stay a str"
    for tid, cnt in pending.items():
        assert cnt == 1, f"tool_call {tid} answered {cnt} times"


def _is_reference_path(path: str) -> bool:
    p = path.replace("\\", "/")
    if any(mk in p for mk in _REF_MARKERS):
        return True
    base = Path(p).name
    return (
        base in ("strategist.md", "executor-base.md", "shared-standards.md")
        or ("references/" in p and base.startswith("image-"))
        or "/modes/" in p
        or "/visual-styles/" in p
    )


def _compact_history(messages: list[dict[str, Any]], project_path: str | None, model: str) -> int:
    """Shrink stale bulky content in ``messages`` in place; returns chars freed.

    Only message *content strings* (and, for non-Gemini models, the
    ``arguments`` string of already-persisted ``write_file`` calls) are
    rewritten. No message is removed, reordered or merged, and ids/keys are
    never touched. Idempotent: already-compacted content is skipped.
    """
    if not messages:
        return 0

    call_index: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {}
    last_assistant_idx = -1
    assistant_idxs: list[int] = []
    for i, m in enumerate(messages):
        if m.get("role") == "assistant":
            assistant_idxs.append(i)
            for tc in m.get("tool_calls") or []:
                try:
                    a = json.loads(tc["function"].get("arguments") or "{}")
                except Exception:
                    a = {}
                call_index[tc["id"]] = (tc["function"].get("name", ""), a if isinstance(a, dict) else {}, tc)
            if m.get("tool_calls"):
                last_assistant_idx = i
    # Protected tail: from the last tool-calling assistant message (and the 2 assistant turns before the end)
    tail_start = last_assistant_idx if last_assistant_idx >= 0 else len(messages)
    if len(assistant_idxs) >= 2:
        tail_start = min(tail_start, assistant_idxs[-2])

    spec_ready = False
    if project_path:
        pp = Path(project_path)
        spec_ready = (pp / "design_spec.md").is_file() and (pp / "spec_lock.md").is_file()

    # Latest read index per path (for superseded-read compaction)
    latest_read: dict[str, int] = {}
    for i, m in enumerate(messages):
        if m.get("role") == "tool" and m.get("tool_call_id") in call_index:
            name, a, _tc = call_index[m["tool_call_id"]]
            if name == "read_file" and isinstance(a.get("path"), str):
                latest_read[a["path"]] = i

    freed = 0
    gemini = _is_gemini_model(model)

    for i, m in enumerate(messages):
        if i >= tail_start:
            break
        role = m.get("role")

        if role == "tool":
            content = m.get("content")
            if not isinstance(content, str) or len(content) <= _COMPACT_MIN_LEN or _COMPACTED_PREFIX in content[:300] or '"compacted": true' in content[:300]:
                continue
            info = call_index.get(m.get("tool_call_id", ""))
            if not info:
                continue
            name, a, _tc = info
            path = a.get("path") if isinstance(a.get("path"), str) else ""
            new: str | None = None
            if name == "read_file" and path:
                base = Path(path).name
                superseded = latest_read.get(path, i) > i
                is_spec = base in ("design_spec.md", "spec_lock.md") and "/references/" not in path
                is_ref = _is_reference_path(path) and not is_spec
                is_svg = base.lower().endswith(".svg")
                should = False
                if is_ref:
                    should = spec_ready or (len(content) > 20000 and i < tail_start - 4)
                elif is_spec:
                    should = superseded
                elif is_svg:
                    should = len(content) > 4000 and superseded
                if should:
                    reason = "superseded by a later read" if (superseded and not is_ref) else "read earlier; call read_file again if needed"
                    new = json.dumps({"path": path, "content": f"[compacted: {len(content)} chars of {base} — {reason}]", "compacted": True})
            elif name == "list_dir":
                try:
                    n = len(json.loads(content).get("entries", []))
                except Exception:
                    n = -1
                new = json.dumps({"compacted": True, "path": a.get("path", ""), "note": f"[compacted list_dir: {n} entries; call list_dir again with pattern= if needed]"})
            elif name in ("run_script", "web_fetch") and len(content) > 6000:
                new = content[:1500] + f"\n…[compacted {len(content) - 3000} chars]…\n" + content[-1500:]
                new = json.dumps({"compacted": True, "output": new})
            if new is not None and len(new) < len(content):
                freed += len(content) - len(new)
                m["content"] = new

        elif role == "assistant" and not gemini:
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                if fn.get("name") != "write_file":
                    continue
                args_s = fn.get("arguments") or ""
                if len(args_s) <= 8000 or "[written to disk" in args_s[:400]:
                    continue
                try:
                    a = json.loads(args_s)
                except Exception:
                    continue
                path = a.get("path") if isinstance(a, dict) else None
                content = a.get("content") if isinstance(a, dict) else None
                if not isinstance(path, str) or not isinstance(content, str):
                    continue
                if not (path.lower().endswith(".svg") or path.lower().endswith(".md")):
                    continue
                # Only if the paired tool result exists and reports success
                result_ok = False
                for later in messages[i + 1 :]:
                    if later.get("role") == "tool" and later.get("tool_call_id") == tc.get("id"):
                        rc = later.get("content") or ""
                        result_ok = isinstance(rc, str) and '"error"' not in rc[:200]
                        break
                if not result_ok:
                    continue
                new_args = json.dumps({"path": path, "content": f"[written to disk: {len(content.encode())} bytes — content elided from history]"})
                freed += len(args_s) - len(new_args)
                fn["arguments"] = new_args
    return freed



def _has_pipeline_progress(project_path: str | None, since: float | None = None) -> bool:
    """Return True if the project directory shows any sign of real pipeline work.

    This is fix plan Step 2's broadened artifact/progress check. It is
    deliberately kept **separate** from the ``exports/*.pptx`` check at the
    end of ``run_job`` — that check is specifically about whether a PPTX has
    been exported (deliberately deferred to download time per
    ``skill_prompt.py``, "Do NOT run svg_to_pptx.py"), and must not be reused
    here or every normal successful run would be misclassified as incomplete.

    Checks, in order:
      1. Any SVG under ``svg_output/`` (main pipeline mid/post-generation).
      2. Both ``design_spec.md`` and ``spec_lock.md`` present (covers the
         ``refine-spec`` workflow legitimately pausing before SVG generation,
         and the normal pipeline's Strategist phase).
      3. Generic fallback: any file under the project directory modified
         after ``since`` (covers workflows not explicitly enumerated above,
         e.g. ``native-enhance-pptx``, ``beautify-pptx``).
    """
    if not project_path:
        return False
    proj = Path(project_path)
    if not proj.is_dir():
        return False

    svg_dir = proj / "svg_output"
    if svg_dir.is_dir() and any(svg_dir.glob("*.svg")):
        return True

    if (proj / "design_spec.md").is_file() and (proj / "spec_lock.md").is_file():
        return True

    if since is not None:
        for f in proj.rglob("*"):
            if not f.is_file():
                continue
            try:
                if f.stat().st_mtime > since:
                    return True
            except OSError:
                continue

    return False


def _fallback_project_path(since: float) -> str | None:
    """Best-effort: find the most recently modified project directory.

    Used only when the job's tracked ``project_path`` (``store.set_project_path``)
    is still unset — e.g. a workflow that writes directly via ``run_script``
    rather than the ``write_file`` tool, so neither the SVG nor the spec-file
    tracking in ``web/tools.py`` ever fires. Only considers directories
    modified after ``since`` so it cannot pick up an unrelated pre-existing
    project.
    """
    if not PROJECTS_DIR.is_dir():
        return None
    candidates: list[tuple[float, Path]] = []
    for child in PROJECTS_DIR.iterdir():
        if not child.is_dir() or child.name == "uploads" or child.name.startswith("."):
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        if mtime > since:
            candidates.append((mtime, child))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return str(candidates[0][1])


_CANVAS_VIEWBOX: dict[str, str] = {
    "ppt169": "0 0 1280 720",
    "ppt43": "0 0 1024 768",
}


def _slugify_topic(topic: str, max_len: int = 40) -> str:
    s = re.sub(r"[^\w]+", "_", (topic or "").strip().lower(), flags=re.UNICODE).strip("_")
    return s[:max_len].strip("_") or "deck"


async def _run_cli(job_id: str, store: JobStore, *argv: str) -> tuple[int, str]:
    """Run ``python3 <argv...>`` from REPO_ROOT, log a short tail, return (rc, output)."""
    cmd = ["python3", *argv]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(REPO_ROOT),
        env={**os.environ},
    )
    out, _ = await proc.communicate()
    text = out.decode(errors="replace")
    await store.append_log(job_id, f"\n$ {' '.join(cmd)}\n{text[-800:]}\n")
    return proc.returncode or 0, text


async def _preinit_project(
    job_id: str,
    topic: str,
    canvas_format: str,
    uploaded_files: list[dict],
    theme: str,
    store: JobStore,
) -> dict | None:
    """SKILL.md Step 2 (init/import) + Step 3 (brand template) done in Python.

    Returns a dict describing the project, or None to fall back to the legacy prompt.
    """
    pm = "skills/ppt-master/scripts/project_manager.py"
    name = _slugify_topic(topic)
    rc, out = await _run_cli(job_id, store, pm, "init", name, "--format", canvas_format, "--dir", str(PROJECTS_DIR))
    if rc != 0 and "already exists" in out:  # same-day re-run of the same topic
        name = f"{name}_{job_id[:6]}"
        rc, out = await _run_cli(job_id, store, pm, "init", name, "--format", canvas_format, "--dir", str(PROJECTS_DIR))
    m = re.search(r"Project created:\s*(\S.*)", out)
    if rc != 0 or not m:
        return None  # unsupported canvas etc. -> legacy path
    project = Path(m.group(1).strip())
    store.set_project_path(job_id, str(project))

    # Brand template == SKILL.md Step 3 'brand' dispatch, done here (no agent turns)
    brand_applied = False
    if theme in _BRAND_PATHS:
        src = REPO_ROOT / _BRAND_PATHS[theme]
        if src.is_dir():
            shutil.copytree(src, project / "templates", dirs_exist_ok=True)
            (project / "images").mkdir(exist_ok=True)
            for f in list((project / "templates").rglob("*")):
                if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
                    shutil.move(str(f), str(project / "images" / f.name))
            brand_applied = (project / "templates" / "design_spec.md").is_file()

    # Uploads -> sources/ with --copy (NOT --move): _build_user_message still reads the
    # uploaded image bytes from uploaded_files[].path for the multimodal blocks.
    if uploaded_files:
        await _run_cli(job_id, store, pm, "import-sources", str(project), *[f["path"] for f in uploaded_files], "--copy")
    src_dir = project / "sources"
    sources = sorted(p.name for p in src_dir.iterdir() if p.is_file()) if src_dir.is_dir() else []
    return {"path": str(project), "canvas": canvas_format, "brand": brand_applied, "sources": sources}


def _project_block(project: dict) -> str:
    canvas = project.get("canvas", "")
    viewbox = _CANVAS_VIEWBOX.get(canvas, "see spec_lock.md canvas.viewBox")
    pp = project["path"]
    sources = project.get("sources") or []
    sources_s = ", ".join(sources) if sources else f"(empty - topic-only; write research notes to {pp}/sources/<slug>.md if useful)"
    brand_s = (
        "applied: templates/design_spec.md (kind: brand) is the locked identity - read it ONCE at Step 4"
        if project.get("brand")
        else "none (free design)"
    )
    return (
        "PROJECT ALREADY INITIALIZED — SKILL.md Step 2 (init/import) and Step 3 (template) are DONE.\n"
        "Do NOT run project_manager.py, do NOT list_dir/read skills/ppt-master/templates/brands/*, do NOT copy brand files.\n"
        f"  project_path = {pp}\n"
        f"  canvas       = {canvas} (viewBox {viewbox})\n"
        f"  sources/     = {sources_s}\n"
        f"  brand        = {brand_s}\n"
        "Path rules: use absolute /app/... paths. (/projects/... is auto-mapped to /app/projects/... but never write outside <project_path>.)\n"
        "Command cheat-sheet (run_script, script path relative to /app, args as array):\n"
        '  icons   : skills/ppt-master/scripts/icon_sync.py  [<project_path>, "tabler-outline/chart-bar", "tabler-outline/users", ...]  '
        "# guess common Tabler names; unknown ones are reported -> re-pick. To search use list_dir(path=\".../icons/tabler-outline\", pattern=\"chart\"); NEVER list a whole icon library.\n"
        "  images  : skills/ppt-master/scripts/analyze_images.py [<project_path>/images]   (only if images/ has files)\n"
        '  gen img : skills/ppt-master/scripts/image_gen.py ["--manifest", "<project_path>/images/image_prompts.json"]   (only if confirmed image_usage includes ai)\n'
        "  qc      : skills/ppt-master/scripts/svg_quality_checker.py [<project_path>]\n"
        "  finalize: skills/ppt-master/scripts/finalize_svg.py [<project_path>]\n"
        "Start now: read references/strategist.md + templates/design_spec_reference.md -> confirm_gate -> "
        "write design_spec.md + spec_lock.md -> icon_sync -> (images) -> generate_slides_batch."
    )


def _slide_ready_hint() -> str:
    if slide_subagent_enabled():
        return (
            "Generate slides with generate_slides_batch (it emits slide_ready itself); "
            "call slide_ready only for SVGs you wrote yourself with write_file."
        )
    return "Use the slide_ready tool after writing each SVG page."


def _build_user_message(
    topic: str,
    canvas_format: str,
    model: str,
    uploaded_files: list[dict],
    theme: str = "none",
    slide_count_pref: str = "",
    image_usage_pref: str = "",
    project: dict | None = None,
) -> str | list:
    """Build the initial user message for the agent."""
    parts = []

    if project:
        parts.append(_project_block(project))
    elif uploaded_files:
        file_list = "\n".join(f"  - {f['path']} ({f['type']})" for f in uploaded_files)
        parts.append(f"Source files have been uploaded to /app/projects/uploads/:\n{file_list}")

    parts.append(
        f"Please create a PowerPoint presentation about: {topic}\n"
        f"Canvas format: {canvas_format}\n"
        f"Follow the full PPT Master pipeline from SKILL.md.\n"
        f"Use the confirm_gate tool at Step 4 (Eight Confirmations).\n"
        f"{_slide_ready_hint()}\n"
        f"Run finalize_svg.py when done. Do NOT run svg_to_pptx.py — "
        f"PPTX export is handled on demand by the server when the user clicks Download."
    )

    if theme != "none" and theme in _BRAND_PATHS and not project:
        parts.append(
            f"Brand template path: {_BRAND_PATHS[theme]}\n"
            f"Apply this brand at SKILL.md Step 3 using the explicit path above."
        )

    if slide_count_pref:
        parts.append(
            f"User's target slide count preference: {slide_count_pref}. "
            f"This is a preference only — reconcile it with the source content volume and "
            f"the confirmed delivery purpose at Step 4 (Eight Confirmations). The Strategist's "
            f"content-aware recommendation and the user's final choice at the confirm_gate step "
            f"still govern the actual page count; do not force this count if it conflicts with "
            f"sound content planning — surface the tension to the user via the confirm_gate recommendation instead."
        )

    if image_usage_pref:
        _image_usage_labels = {
            "ai": "AI-generated images",
            "provided": "the user's own uploaded files only (no AI generation, no web sourcing)",
            "web": "web-sourced images",
            "none": "no images at all",
        }
        _label = _image_usage_labels.get(image_usage_pref, image_usage_pref)
        parts.append(
            f"User's image usage preference: {_label}. "
            f"Set `recommend.image_usage` (and, if the Eight Confirmations page is shown, the confirm_gate recommendation) "
            f"to honor this at Step 4. IMPORTANT: if auto-approve is active, your own `recommendations` dict passed to the "
            f"confirm_gate tool becomes the final confirmed value with no human review — make sure `image_usage` in that "
            f"dict matches this preference exactly (map to the pipeline's image_usage ids: 'ai', 'provided', 'web', or 'none')."
        )

    joined_text = "\n\n".join(parts)

    image_files = [f for f in uploaded_files if f.get("type", "").startswith("image/")]
    if not image_files:
        return joined_text

    content_blocks: list[dict] = [{"type": "text", "text": joined_text}]
    for f in image_files:
        try:
            data = Path(f["path"]).read_bytes()
        except FileNotFoundError:
            print(f"WARNING: Image file not found, skipping: {f['path']}")
            continue
        b64 = base64.b64encode(data).decode()
        data_url = f"data:{f['type']};base64,{b64}"
        content_blocks.append({"type": "text", "text": f"[Image: {f['name']}]"})
        content_blocks.append({"type": "image_url", "image_url": {"url": data_url}})

    return content_blocks


async def run_job(
    job_id: str,
    topic: str,
    canvas_format: str,
    model: str,
    uploaded_files: list[dict],
    store: JobStore,
    theme: str = "none",
    slide_count_pref: str = "",
    image_usage_pref: str = "",
    auto_confirm: bool = False,
) -> None:
    """
    Main agent loop. Runs as an asyncio task.
    Drives the full PPT Master pipeline via OpenAI-compatible tool-calling.
    """
    agent_api_key = os.environ.get("AGENT_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    agent_base_url = os.environ.get("AGENT_BASE_URL") or os.environ.get("OPENAI_BASE_URL", "")

    client = AsyncOpenAI(
        api_key=agent_api_key,
        base_url=agent_base_url,
        default_headers={
            "User-Agent": "ppt-master/1.0",
        },
    )

    system_prompt = get_system_prompt()

    store.set_status(job_id, "running")
    await store.append_log(job_id, f"[Agent] Starting job: {topic}\n")
    await store.append_log(job_id, f"[Agent] Model: {model}\n")
    await store.append_log(job_id, f"[Agent] Canvas: {canvas_format}\n")
    await store.append_log(job_id, f"[Agent] Theme: {theme}\n")
    await store.append_log(job_id, f"[Agent] Auto-confirm: {auto_confirm}\n")
    await store.append_log(job_id, f"[Agent] Base URL: {agent_base_url}\n")
    await store.append_log(job_id, f"[Agent] API key prefix: {agent_api_key[:12]}...\n\n")

    project_info: dict | None = None
    try:
        project_info = await _preinit_project(job_id, topic, canvas_format, uploaded_files, theme, store)
        if project_info:
            await store.append_log(job_id, f"[Agent] Project pre-initialized: {project_info['path']}\n")
        else:
            await store.append_log(job_id, "[Agent] Project pre-init unavailable; falling back to agent-driven init\n")
    except Exception as exc:  # noqa: BLE001
        project_info = None
        await store.append_log(job_id, f"[Agent] Project pre-init failed: {exc}\n")

    user_message = _build_user_message(
        topic, canvas_format, model, uploaded_files, theme, slide_count_pref, image_usage_pref, project=project_info
    )
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": get_skill_md()},
        {"role": "assistant", "content": "I have read the SKILL.md pipeline. I am ready to begin."},
        {"role": "user", "content": user_message},
    ]

    max_iterations = 200  # safety cap
    iteration = 0
    # Taken AFTER pre-init so files created by pre-init don't count as pipeline progress.
    job_start_ts = time.time()
    stall_count = 0  # consecutive non-productive turns (fix plan Step 4)
    last_retry_cause: str | None = None  # "truncation" | "no_progress" | "unexpected"

    use_non_streaming = _is_gemini_model(model)
    tool_defs = get_tool_definitions()
    ctx = ToolContext(
        client=client,
        model=model,
        auto_confirm=auto_confirm,
        slide_model=os.environ.get("SLIDE_SUBAGENT_MODEL") or None,
    )

    async def _report_llm_error(exc: Exception) -> None:
        full_tb = traceback.format_exc()
        response_body = ""
        if hasattr(exc, "response") and exc.response is not None:
            try:
                response_body = f"\nHTTP {exc.response.status_code}: {exc.response.text[:500]}"
            except Exception:
                pass
        error_msg = f"[Agent] LLM call failed: {exc}{response_body}\n{full_tb}\n"
        await store.append_log(job_id, error_msg)
        store.set_error(job_id, str(exc))
        await store.push_event(job_id, {"type": "error", "message": str(exc)})

    while iteration < max_iterations:
        iteration += 1

        # Cooperative cancellation check — runs before every LLM call so a
        # delete request is honoured at the start of the next iteration.
        if store.is_cancelled(job_id):
            await store.append_log(job_id, "\n[Agent] Job cancelled.\n")
            store.set_status(job_id, "error")
            store.set_error(job_id, "Job cancelled by user")
            return

        await store.append_log(job_id, f"\n[Agent] Iteration {iteration}...\n")

        # Context-history compaction (deterministic + idempotent): stub out stale
        # reference-doc reads and already-on-disk SVG writes before each call.
        try:
            _job_meta = store.get_job(job_id)
            freed = _compact_history(messages, (_job_meta or {}).get("project_path"), model)
            if freed:
                await store.append_log(job_id, f"[Agent] Context compaction freed {freed:,} chars\n")
        except Exception as exc:  # never let compaction break the pipeline
            await store.append_log(job_id, f"[Agent] Context compaction skipped: {exc}\n")
        await store.append_log(job_id, f"[Agent] History size: {_history_chars(messages):,} chars / {len(messages)} msgs\n")

        outgoing = [{"role": "system", "content": system_prompt}] + _strip_thinking_content_blocks(messages)

        async def _call_llm_once(max_tokens: int) -> tuple[str, list[dict[str, Any]], str | None]:
            """Perform one LLM call (streaming or non-streaming).

            Returns ``(text_buffer, tool_calls_list, finish_reason)``. Raises
            on transport/API error — the caller handles retry/failure.
            """
            local_text = ""
            local_tool_calls: list[dict[str, Any]] = []
            local_finish_reason: str | None = None

            if use_non_streaming:
                # ── Non-streaming path (Gemini) ───────────────────────────
                # Gemini thinking models attach ``thought_signature`` to tool
                # calls.  We use non-streaming so the full response object is
                # returned intact, then capture ``thought_signature`` from
                # ``model_extra`` below and round-trip it in the history.
                # This avoids the "missing a thought_signature" HTTP 400.
                response = await client.chat.completions.create(
                    model=model,
                    messages=outgoing,
                    tools=tool_defs,
                    tool_choice="auto",
                    max_tokens=max_tokens,
                    stream=False,
                )
                choice = response.choices[0] if response.choices else None
                if choice is not None:
                    local_finish_reason = choice.finish_reason
                    msg = choice.message
                    local_text = msg.content or ""
                    if local_text:
                        await store.append_log(job_id, local_text)

                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            tc_dict: dict[str, Any] = {
                                "id": tc.id or "",
                                "type": "function",
                                "function": {
                                    "name": tc.function.name or "",
                                    "arguments": tc.function.arguments or "",
                                },
                            }
                            # Preserve thought_signature and any other
                            # Gemini-specific extra fields so the history
                            # round-trips correctly on the next turn.
                            # Guard with hasattr+truthiness: model_extra may be
                            # None or {} depending on the Pydantic version.
                            if hasattr(tc, "model_extra") and tc.model_extra:
                                tc_dict.update(tc.model_extra)
                            if hasattr(tc, "function") and hasattr(tc.function, "model_extra") and tc.function.model_extra:
                                tc_dict["function"].update(tc.function.model_extra)
                            local_tool_calls.append(tc_dict)

            else:
                # ── Streaming path (non-Gemini) ───────────────────────────
                tool_calls_raw: dict[int, dict] = {}  # index → partial tool call

                stream = await client.chat.completions.create(
                    model=model,
                    messages=outgoing,
                    tools=tool_defs,
                    tool_choice="auto",
                    max_tokens=max_tokens,
                    stream=True,
                )

                async for chunk in stream:
                    choice = chunk.choices[0] if chunk.choices else None
                    if choice is None:
                        continue

                    delta = choice.delta

                    # Track finish_reason across all chunks (fix plan Step 1)
                    # — previously only checked once, inline, to end the read
                    # loop on "stop". Now captured regardless of value so
                    # "length" (truncation) and any other terminal reason are
                    # visible to the completion-detection logic below.
                    if choice.finish_reason:
                        local_finish_reason = choice.finish_reason

                    # Collect text
                    if delta.content:
                        local_text += delta.content
                        await store.append_log(job_id, delta.content)

                    # Collect tool call fragments
                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_calls_raw:
                                tool_calls_raw[idx] = {
                                    "id": tc_delta.id or "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            if tc_delta.id:
                                tool_calls_raw[idx]["id"] = tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    tool_calls_raw[idx]["function"]["name"] += tc_delta.function.name
                                if tc_delta.function.arguments:
                                    tool_calls_raw[idx]["function"]["arguments"] += tc_delta.function.arguments

                    if choice.finish_reason == "stop":
                        break

                local_tool_calls = [tool_calls_raw[i] for i in sorted(tool_calls_raw.keys())]

            return local_text, local_tool_calls, local_finish_reason

        text_buffer = ""
        tool_calls_list: list[dict[str, Any]] = []
        finish_reason: str | None = None

        primary_max_tokens = _max_tokens_for(model)

        try:
            await store.append_log(
                job_id,
                f"[Agent] Calling {model} at {agent_base_url} with {len(messages)} messages" f" ({'non-streaming' if use_non_streaming else 'streaming'}, max_tokens={primary_max_tokens})\n",
            )
            text_buffer, tool_calls_list, finish_reason = await _call_llm_once(primary_max_tokens)
        except Exception as exc:
            if primary_max_tokens > 16000 and _is_token_limit_error(exc):
                # Step 5 — narrow, one-shot retry at the conservative cap
                # before treating this as a hard failure. Avoids turning
                # "sometimes truncates" into "hard-fails immediately" for
                # models with a lower real ceiling than _max_tokens_for assumed.
                await store.append_log(
                    job_id,
                    f"[Agent] HTTP 400 looks like a token/max_tokens limit problem at max_tokens={primary_max_tokens}; " f"retrying once at the conservative 16000 cap...\n",
                )
                try:
                    text_buffer, tool_calls_list, finish_reason = await _call_llm_once(16000)
                except Exception as exc2:
                    await _report_llm_error(exc2)
                    return
            else:
                await _report_llm_error(exc)
                return

        # ── Build assistant message ───────────────────────────────────────────
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": text_buffer or None}
        if tool_calls_list:
            assistant_msg["tool_calls"] = tool_calls_list

        messages.append(assistant_msg)

        # ── Completion decision (fix plan Step 3) ───────────────────────────
        # Replaces the old bare "no tool_calls ⇒ done" check, which
        # misreported both truncation (finish_reason == "length") and an
        # early, no-progress stop as successful completion.
        if not tool_calls_list:
            job_meta = store.get_job(job_id)
            current_project_path = job_meta.get("project_path") if job_meta else None
            if not current_project_path:
                current_project_path = _fallback_project_path(job_start_ts)
            has_progress = _has_pipeline_progress(current_project_path, since=job_start_ts)

            if finish_reason == "length":
                # Truncated mid-thought by the token cap — not a real stop.
                stall_count += 1
                last_retry_cause = "truncation"
                await store.append_log(
                    job_id,
                    f"\n[Agent] Response was truncated by the token limit (finish_reason=length) — " f"retrying ({stall_count}/{STALL_THRESHOLD}).\n",
                )
                if stall_count >= STALL_THRESHOLD:
                    fail_msg = (
                        "Agent repeatedly hit the token limit before completing its turn, even after retries. "
                        "This may mean the current step is too large for a single turn — "
                        "try narrowing the request or re-running with a smaller scope."
                    )
                    await store.append_log(job_id, f"[Agent] Stall limit reached — failing.\n[Agent] {fail_msg}\n")
                    store.set_status(job_id, "error")
                    store.set_error(job_id, fail_msg)
                    return
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was cut off by the token limit before you " "finished. Continue exactly where you left off — if you were about " "to call a tool, call it now."
                        ),
                    }
                )
                continue

            elif finish_reason == "stop" and has_progress:
                # Genuine completion: the model stopped cleanly AND left
                # behind real pipeline artifacts (SVGs, or at least a spec).
                await store.append_log(job_id, "\n[Agent] Pipeline complete.\n")
                break

            else:
                # Either finish_reason == "stop" with no progress at all
                # (confused / stalled before doing any real work), or some
                # other/unexpected finish_reason (content_filter, null, etc).
                stall_count += 1
                last_retry_cause = "no_progress" if finish_reason == "stop" else "unexpected"
                if finish_reason == "stop":
                    await store.append_log(
                        job_id,
                        f"\n[Agent] Model stopped without producing any pipeline artifacts " f"(no project, spec, or slides found) — retrying ({stall_count}/{STALL_THRESHOLD}).\n",
                    )
                else:
                    await store.append_log(
                        job_id,
                        f"\n[Agent] Turn ended with an unexpected finish_reason={finish_reason!r} and no tool calls — " f"retrying ({stall_count}/{STALL_THRESHOLD}).\n",
                    )

                if stall_count >= STALL_THRESHOLD:
                    if last_retry_cause == "no_progress":
                        fail_msg = (
                            "Agent stopped repeatedly without producing output — this often means "
                            "the request was ambiguous or missing required source material. Try "
                            "rephrasing the topic or attaching source files."
                        )
                    else:
                        fail_msg = (
                            f"Agent stopped repeatedly with an unexpected finish_reason "
                            f"({finish_reason!r}) and no tool calls or pipeline output. "
                            f"This may indicate an upstream API or content-filtering issue — try again "
                            f"or adjust the request."
                        )
                    await store.append_log(job_id, f"[Agent] Stall limit reached — failing.\n[Agent] {fail_msg}\n")
                    store.set_status(job_id, "error")
                    store.set_error(job_id, fail_msg)
                    return

                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You stopped without making progress on the pipeline (no project, "
                            "spec, or slides were produced). Please continue the pipeline from "
                            "SKILL.md, or if the request is ambiguous or missing required "
                            "information, clearly state what is needed."
                        ),
                    }
                )
                continue

        # ── Execute tool calls ────────────────────────────────────────────────
        for tc in tool_calls_list:
            # A tool call is actually being executed this turn — reset the
            # stall counter (fix plan Step 4).
            stall_count = 0
            last_retry_cause = None

            tool_name = tc["function"]["name"]
            try:
                tool_args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                tool_args = {}

            await store.append_log(job_id, f"\n[Tool] {tool_name}({json.dumps(tool_args)[:200]})\n")

            try:
                result = await dispatch_tool(tool_name, tool_args, job_id, store, auto_confirm=auto_confirm, ctx=ctx)
            except Exception as exc:
                result = {"error": f"Tool execution failed: {exc}"}

            await store.append_log(job_id, f"[Tool result] {json.dumps(result)[:300]}\n")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result),
                }
            )

    # ── Signal pipeline completion ────────────────────────────────────────────
    # The PPTX is generated on demand when the user clicks Download.
    # No pre-built copy is made; svg_to_pptx.py runs at download time.
    job_meta = store.get_job(job_id)
    project_path_str = job_meta.get("project_path") if job_meta else None
    pptx_files: list[Path] = []
    if project_path_str:
        proj_exports = Path(project_path_str) / "exports"
        if proj_exports.is_dir():
            pptx_files = sorted(
                proj_exports.glob("*.pptx"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )

    if pptx_files:
        latest = pptx_files[0]
        store.set_status(job_id, "done")
        await store.push_event(
            job_id,
            {
                "type": "job_done",
                "download_url": f"/jobs/{job_id}/download",
                "filename": latest.name,
            },
        )
    else:
        store.set_status(job_id, "done")
        await store.push_event(
            job_id,
            {
                "type": "job_done",
                "download_url": f"/jobs/{job_id}/download",
                "message": "Pipeline complete. PPTX will be generated on demand.",
            },
        )
