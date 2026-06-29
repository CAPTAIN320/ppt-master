"""
Agent runner — drives the PPT Master pipeline via LLM tool-calling.

Uses the OpenAI-compatible API pointing to the LiteLLM proxy.

The agent reads SKILL.md (+ web-mode preamble) as its system prompt and
executes the full serial pipeline:
  project init → source ingestion → Strategist → Eight Confirmations →
  SVG generation → post-processing → PPTX export
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from .job_store import JobStore
from .skill_prompt import get_skill_md, get_system_prompt
from .tools import TOOL_DEFINITIONS, dispatch_tool

REPO_ROOT = Path("/app")


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
            msg["content"] = [
                block
                for block in msg["content"]
                if not (
                    isinstance(block, dict)
                    and block.get("type") in ("thinking", "thought")
                )
            ]
            # If all blocks were thinking blocks, collapse to None so the
            # message stays valid (assistant with only tool_calls).
            if not msg["content"]:
                msg["content"] = None

        sanitized.append(msg)
    return sanitized


def _is_gemini_model(model: str) -> bool:
    """Return True if the model name indicates a Gemini model."""
    return "gemini" in model.lower()


def _build_user_message(
    topic: str,
    canvas_format: str,
    model: str,
    uploaded_files: list[dict],
) -> str:
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
        f"Export the final PPTX to /app/exports/ when done."
    )

    return "\n\n".join(parts)


async def run_job(
    job_id: str,
    topic: str,
    canvas_format: str,
    model: str,
    uploaded_files: list[dict],
    store: JobStore,
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
    user_message = _build_user_message(topic, canvas_format, model, uploaded_files)

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": get_skill_md()},
        {"role": "assistant", "content": "I have read the SKILL.md pipeline. I am ready to begin."},
        {"role": "user", "content": user_message},
    ]

    store.set_status(job_id, "running")
    await store.append_log(job_id, f"[Agent] Starting job: {topic}\n")
    await store.append_log(job_id, f"[Agent] Model: {model}\n")
    await store.append_log(job_id, f"[Agent] Canvas: {canvas_format}\n")
    await store.append_log(job_id, f"[Agent] Base URL: {agent_base_url}\n")
    await store.append_log(job_id, f"[Agent] API key prefix: {agent_api_key[:12]}...\n\n")

    max_iterations = 200  # safety cap
    iteration = 0

    use_non_streaming = _is_gemini_model(model)

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

        text_buffer = ""
        tool_calls_list: list[dict[str, Any]] = []

        try:
            await store.append_log(
                job_id,
                f"[Agent] Calling {model} at {agent_base_url} with {len(messages)} messages"
                f" ({'non-streaming' if use_non_streaming else 'streaming'})\n",
            )

            if use_non_streaming:
                # ── Non-streaming path (Gemini) ───────────────────────────────
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
                    max_tokens=16000,
                    stream=False,
                )
                choice = response.choices[0] if response.choices else None
                if choice is not None:
                    msg = choice.message
                    text_buffer = msg.content or ""
                    if text_buffer:
                        await store.append_log(job_id, text_buffer)

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
                            tool_calls_list.append(tc_dict)

            else:
                # ── Streaming path (non-Gemini) ───────────────────────────────
                tool_calls_raw: dict[int, dict] = {}  # index → partial tool call

                stream = await client.chat.completions.create(
                    model=model,
                    messages=outgoing,
                    tools=TOOL_DEFINITIONS,
                    tool_choice="auto",
                    max_tokens=16000,
                    stream=True,
                )

                async for chunk in stream:
                    choice = chunk.choices[0] if chunk.choices else None
                    if choice is None:
                        continue

                    delta = choice.delta

                    # Collect text
                    if delta.content:
                        text_buffer += delta.content
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

                tool_calls_list = [tool_calls_raw[i] for i in sorted(tool_calls_raw.keys())]

        except Exception as exc:
            import traceback

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
            return

        # ── Build assistant message ───────────────────────────────────────────
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": text_buffer or None}
        if tool_calls_list:
            assistant_msg["tool_calls"] = tool_calls_list

        messages.append(assistant_msg)

        # ── No tool calls → agent is done ─────────────────────────────────────
        if not tool_calls_list:
            await store.append_log(job_id, "\n[Agent] Pipeline complete.\n")
            break

        # ── Execute tool calls ────────────────────────────────────────────────
        for tc in tool_calls_list:
            tool_name = tc["function"]["name"]
            try:
                tool_args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                tool_args = {}

            await store.append_log(job_id, f"\n[Tool] {tool_name}({json.dumps(tool_args)[:200]})\n")

            try:
                result = await dispatch_tool(tool_name, tool_args, job_id, store)
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

    # ── Find and signal the exported PPTX ────────────────────────────────────
    # Look for the PPTX in the project's own exports/ subdirectory.
    # No copy to the global exports/ directory is performed.
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
