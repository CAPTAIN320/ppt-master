"""
Shared LLM helpers for the web layer.

Holds the small model-capability helpers that used to live in
``agent_runner.py`` (so ``slide_subagent.py`` can import them without a
circular import) plus ``call_llm_text`` — an isolated, tool-less chat
completion used by the per-slide sub-agent.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any


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
    via the non-streaming path which preserves ``thought_signature`` from
    ``model_extra``.
    """
    sanitized: list[dict[str, Any]] = []
    for msg in messages:
        msg = dict(msg)  # shallow copy — don't mutate the live history

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

    Used only to decide whether a single conservative-cap retry is
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


def _is_transient_error(exc: Exception) -> bool:
    """429 / 5xx / connection / timeout errors worth a backoff retry."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return True
    name = type(exc).__name__
    return name in ("APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError", "TimeoutError", "ConnectError", "ReadTimeout")


class LLMCancelled(Exception):
    """Raised by ``call_llm_text`` when the owning job was cancelled."""


_BACKOFF_SECONDS = (2.0, 5.0, 10.0)


async def call_llm_text(
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int | None = None,
    job_id: str | None = None,
    store: Any = None,
    label: str = "",
) -> tuple[str, str | None]:
    """Isolated, tool-less chat completion.

    Returns ``(text, finish_reason)``.

    * Gemini → ``stream=False``; others → ``stream=True``. No ``tools=`` is
      ever sent, so no ``thought_signature`` round-trip is needed.
    * One automatic retry at the 16000 cap on a token-limit HTTP 400.
    * Up to 3 attempts with exponential backoff on transient errors.
    * Cancellation-aware: polls ``store.is_cancelled(job_id)`` every 2 s while
      the call is in flight and raises ``LLMCancelled`` after cancelling it.
    """
    gemini = _is_gemini_model(model)
    cap = max_tokens or _max_tokens_for(model)

    def _cancelled() -> bool:
        return bool(store is not None and job_id is not None and store.is_cancelled(job_id))

    async def _once(mt: int) -> tuple[str, str | None]:
        if gemini:
            resp = await client.chat.completions.create(model=model, messages=messages, max_tokens=mt, stream=False)
            choice = resp.choices[0] if resp.choices else None
            if choice is None:
                return "", None
            return (choice.message.content or ""), choice.finish_reason
        parts: list[str] = []
        finish: str | None = None
        stream = await client.chat.completions.create(model=model, messages=messages, max_tokens=mt, stream=True)
        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if choice is None:
                continue
            delta = getattr(choice, "delta", None)
            if delta is not None and getattr(delta, "content", None):
                parts.append(delta.content)
            if choice.finish_reason:
                finish = choice.finish_reason
        return "".join(parts), finish

    async def _run_cancellable(mt: int) -> tuple[str, str | None]:
        if _cancelled():
            raise LLMCancelled()
        task = asyncio.ensure_future(_once(mt))
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=2.0)
                if done:
                    return task.result()
                if _cancelled():
                    task.cancel()
                    try:
                        await task
                    except BaseException:
                        pass
                    raise LLMCancelled()
        except asyncio.CancelledError:
            task.cancel()
            raise

    token_retry_done = False
    attempt = 0
    cur_cap = cap
    while True:
        try:
            return await _run_cancellable(cur_cap)
        except LLMCancelled:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if cur_cap > 16000 and not token_retry_done and _is_token_limit_error(exc):
                token_retry_done = True
                cur_cap = 16000
                if store is not None and job_id is not None:
                    await store.append_log(job_id, f"[{label or 'LLM'}] token-limit 400 at {cap}; retrying at 16000\n")
                continue
            if _is_transient_error(exc) and attempt < len(_BACKOFF_SECONDS):
                delay = _BACKOFF_SECONDS[attempt] * (0.8 + 0.4 * random.random())
                attempt += 1
                if store is not None and job_id is not None:
                    await store.append_log(job_id, f"[{label or 'LLM'}] transient error ({type(exc).__name__}); retry {attempt}/3 in {delay:.1f}s\n")
                # sleep in small steps so cancellation stays responsive
                slept = 0.0
                while slept < delay:
                    if _cancelled():
                        raise LLMCancelled()
                    await asyncio.sleep(min(1.0, delay - slept))
                    slept += 1.0
                continue
            raise
