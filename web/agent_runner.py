"""
Agent runner — drives the PPT Master pipeline via LLM tool-calling.

Uses the OpenAI-compatible API pointing to the LiteLLM proxy.

The agent reads SKILL.md (+ web-mode preamble) as its system prompt and
executes the full serial pipeline:
  project init → source ingestion → Strategist → Eight Confirmations →
  SVG generation → post-processing → PPTX export
"""

from __future__ import annotations

import base64
import json
import os
import time
import traceback
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from .job_store import JobStore
from .skill_prompt import get_skill_md, get_system_prompt
from .tools import TOOL_DEFINITIONS, dispatch_tool

REPO_ROOT = Path("/app")
PROJECTS_DIR = REPO_ROOT / "projects"

# Number of consecutive non-productive turns (truncation, no-progress-stop, or
# any other unexpected finish_reason) tolerated before the job is failed with
# an actionable error instead of spinning to max_iterations.
STALL_THRESHOLD = 3

_THEMES: dict[str, str] = {
    "rakuten-crimson": (
        "Brand theme: Rakuten Crimson\n"
        "- Primary: #BF0000 (crimson red)\n"
        "- Background: #FFFFFF (white)\n"
        "- Accent: #777B7E (gray)\n"
        "Lock this palette in §III Visual Theme and §V Layout. Do not deviate.\n"
        "Background MUST be white (#FFFFFF) on all slides. "
        "Do NOT select any dark-background visual style "
        "(dark-tech, blueprint, dark-cinematic, chalkboard, ink-wash, pixel-art, "
        "or any style with a dark/black canvas). "
        "Choose only light-background styles (swiss-minimal, soft-rounded, editorial, "
        "glassmorphism, photo-editorial, or similar).\n"
        "On every slide, add a footer bar at the bottom of the canvas:\n"
        '- Bottom-left Rakuten wordmark: <text x="40" y="[canvas_height - 28]" font-size="14" font-weight="bold" fill="#BF0000" font-family="sans-serif">Rakuten</text>\n'
        '- Bottom-right confidential label: <text x="[canvas_width - 40]" y="[canvas_height - 28]" font-size="10" fill="#777B7E" text-anchor="end" font-family="sans-serif">Confidential</text>\n'
        "Replace [canvas_height] and [canvas_width] with the actual SVG canvas dimensions for each slide."
    ),
    "rakuten-mobile": (
        "Brand theme: Rakuten Mobile\n"
        "- Primary: #FF008C (Rakuten pink)\n"
        "- Background: #FFFFFF (white)\n"
        "- Accent: #777B7E (gray)\n"
        "Lock this palette in §III Visual Theme and §V Layout. Do not deviate.\n"
        "Background MUST be white (#FFFFFF) on all slides. "
        "Do NOT select any dark-background visual style "
        "(dark-tech, blueprint, dark-cinematic, chalkboard, ink-wash, pixel-art, "
        "or any style with a dark/black canvas). "
        "Choose only light-background styles (swiss-minimal, soft-rounded, editorial, "
        "glassmorphism, photo-editorial, or similar).\n"
        "On every slide, add a footer bar at the bottom of the canvas:\n"
        '- Bottom-left Rakuten wordmark: <text x="40" y="[canvas_height - 28]" font-size="14" font-weight="bold" fill="#FF008C" font-family="sans-serif">Rakuten Mobile</text>\n'
        '- Bottom-right confidential label: <text x="[canvas_width - 40]" y="[canvas_height - 28]" font-size="10" fill="#777B7E" text-anchor="end" font-family="sans-serif">Confidential</text>\n'
        "Replace [canvas_height] and [canvas_width] with the actual SVG canvas dimensions for each slide."
    ),
}


def _strip_thinking_content_blocks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Strip ``thinking``/``thought`` content blocks from the message history.

    Some models (e.g. Claude extended-thinking) embed reasoning blocks inside
    the ``content`` list of assistant messages.  These must be removed before
    re-sending the history or certain providers will reject the request.

    NOTE: This function intentionally does NOT touch ``thought_signature`` on
    tool-call objects.  Gemini thinking models require ``thought_signature`` to
    be PRESENT in the history — stripping it causes the
    "missing a thought_signature" HTTP 400.  Gemini tool calls are assembled
    via the non-streaming path (see ``_call_llm_non_streaming``) which
    preserves ``thought_signature`` from ``model_extra``.
    """
    sanitized: list[dict[str, Any]] = []
    for msg in messages:
        msg = dict(msg)  # shallow copy — don't mutate the live history

        # ── Strip thinking/thought content blocks ─────────────────────────
        if isinstance(msg.get("content"), list):
            msg["content"] = [block for block in msg["content"] if not (isinstance(block, dict) and block.get("type") in ("thinking", "thought"))]
            # If all blocks were thinking blocks, collapse to None so the
            # message stays valid (assistant with only tool_calls).
            if not msg["content"]:
                msg["content"] = None

        sanitized.append(msg)
    return sanitized


def _is_gemini_model(model: str) -> bool:
    """Return True if the model name indicates a Gemini model."""
    return "gemini" in model.lower()


def _max_tokens_for(model: str) -> int:
    """Return the max_tokens cap to use for this model.

    All models currently offered in the UI (claude-sonnet-5, claude-sonnet-4.6,
    gemini-3.1-pro-preview, gpt-5.4) support 32K+ output tokens, so a flat cap
    is used regardless of model name. The `_is_token_limit_error()` fallback in
    the call sites (drop to 16000 once, then hard-fail) still protects against
    the edge case of an env-overridden AGENT_MODEL with a lower real ceiling.
    """
    return 32000


def _is_token_limit_error(exc: Exception) -> bool:
    """Best-effort detection of an HTTP 400 caused by a token/max_tokens problem.

    Used only to decide whether a single conservative-cap retry (Step 5) is
    warranted — intentionally narrow, not a general-purpose retry-everything
    mechanism.
    """
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code != 400:
        return False
    try:
        body = (response.text or "").lower()
    except Exception:
        body = str(exc).lower()
    return any(needle in body for needle in ("max_tokens", "maximum context length", "token limit", "too many tokens", "max tokens"))


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


def _build_user_message(
    topic: str,
    canvas_format: str,
    model: str,
    uploaded_files: list[dict],
    theme: str = "none",
    slide_count_pref: str = "",
    image_usage_pref: str = "",
) -> str | list:
    """Build the initial user message for the agent."""
    parts = []

    if uploaded_files:
        file_list = "\n".join(f"  - {f['path']} ({f['type']})" for f in uploaded_files)
        parts.append(f"Source files have been uploaded to /app/projects/uploads/:\n{file_list}")

    parts.append(
        f"Please create a PowerPoint presentation about: {topic}\n"
        f"Canvas format: {canvas_format}\n"
        f"Follow the full PPT Master pipeline from SKILL.md.\n"
        f"Use the confirm_gate tool at Step 4 (Eight Confirmations).\n"
        f"Use the slide_ready tool after writing each SVG page.\n"
        f"Run finalize_svg.py when done. Do NOT run svg_to_pptx.py — "
        f"PPTX export is handled on demand by the server when the user clicks Download."
    )

    if theme != "none" and theme in _THEMES:
        parts.append(_THEMES[theme])

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
    user_message = _build_user_message(topic, canvas_format, model, uploaded_files, theme, slide_count_pref, image_usage_pref)

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": get_skill_md()},
        {"role": "assistant", "content": "I have read the SKILL.md pipeline. I am ready to begin."},
        {"role": "user", "content": user_message},
    ]

    store.set_status(job_id, "running")
    await store.append_log(job_id, f"[Agent] Starting job: {topic}\n")
    await store.append_log(job_id, f"[Agent] Model: {model}\n")
    await store.append_log(job_id, f"[Agent] Canvas: {canvas_format}\n")
    await store.append_log(job_id, f"[Agent] Theme: {theme}\n")
    await store.append_log(job_id, f"[Agent] Auto-confirm: {auto_confirm}\n")
    await store.append_log(job_id, f"[Agent] Base URL: {agent_base_url}\n")
    await store.append_log(job_id, f"[Agent] API key prefix: {agent_api_key[:12]}...\n\n")

    max_iterations = 200  # safety cap
    iteration = 0
    job_start_ts = time.time()
    stall_count = 0  # consecutive non-productive turns (fix plan Step 4)
    last_retry_cause: str | None = None  # "truncation" | "no_progress" | "unexpected"

    use_non_streaming = _is_gemini_model(model)

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
                    tools=TOOL_DEFINITIONS,
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
                    tools=TOOL_DEFINITIONS,
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
                result = await dispatch_tool(tool_name, tool_args, job_id, store, auto_confirm=auto_confirm)
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
