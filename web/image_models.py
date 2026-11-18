"""Image-model allowlist helpers shared by web/tools.py (enforcement) and web/agent_runner.py (guidance).

Env:
  OPENAI_MODEL          default image model (used by image_gen.py when no model is given)
  IMAGE_MODELS_ALLOWED  comma-separated allowlist (whitespace-trimmed)

Everything here is failure-safe: callers wrap it, and the sanitizers never raise.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Callable

DEFAULT_MODEL = "gpt-image-2.5-flare"
DEFAULT_ALLOWED = ("gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "gemini-3.1-flash-image")

# Neutral one-liners: the LiteLLM config gives no capability description.
_NOTES = {
    "gpt-image-2.5-sunburst": "alternate OpenAI-family model",
    "gemini-3.1-flash-image": "Google alternate",
}


def allowed_models() -> list[str]:
    """Parse IMAGE_MODELS_ALLOWED (comma separated); fall back to the built-in three."""
    raw = os.environ.get("IMAGE_MODELS_ALLOWED") or ""
    out: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out or list(DEFAULT_ALLOWED)


def default_model() -> str:
    return (os.environ.get("OPENAI_MODEL") or "").strip() or DEFAULT_MODEL


def is_allowed(model) -> bool:
    return isinstance(model, str) and model.strip() in allowed_models()


def sanitize_cli_model(args: list[str]) -> tuple[list[str], list[str]]:
    """Drop a disallowed `--model/-m <x>` (or `--model=<x>`) pair. Returns (args, notes)."""
    out: list[str] = []
    notes: list[str] = []
    i = 0
    dflt = default_model()
    while i < len(args):
        a = args[i]
        if a in ("--model", "-m") and i + 1 < len(args):
            val = args[i + 1]
            if not is_allowed(val):
                notes.append(f"[image-model-allowlist] cli: model '{val}' not allowed -> using default '{dflt}'")
                i += 2
                continue
        elif a.startswith("--model=") or a.startswith("-m="):
            val = a.split("=", 1)[1]
            if not is_allowed(val):
                notes.append(f"[image-model-allowlist] cli: model '{val}' not allowed -> using default '{dflt}'")
                i += 1
                continue
        out.append(a)
        i += 1
    return out, notes


def _manifest_arg(args: list[str]) -> str | None:
    for i, a in enumerate(args):
        if a == "--manifest" and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--manifest="):
            return a.split("=", 1)[1]
    return None


def sanitize_manifest(path: Path) -> list[str]:
    """Remove disallowed per-item `model` keys from an image_prompts.json (atomic rewrite). Returns notes."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    notes: list[str] = []
    changed = False
    dflt = default_model()
    for idx, item in enumerate(items):
        if not isinstance(item, dict) or "model" not in item:
            continue
        m = item["model"]
        if m is None or (isinstance(m, str) and not m.strip()):
            item.pop("model")  # unset/blank -> default applies anyway; tidy silently
            changed = True
            continue
        if not is_allowed(m):
            label = item.get("id") or item.get("filename") or f"#{idx}"
            notes.append(f"[image-model-allowlist] item {label}: model '{m}' not allowed -> using default '{dflt}'")
            item.pop("model")
    if notes or changed:
        target = Path(path)
        fd, tmp = tempfile.mkstemp(prefix=target.stem + ".", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return notes


def sanitize_image_gen_call(args: list[str], resolve: Callable[[str], Path]) -> tuple[list[str], list[str]]:
    """Apply the allowlist to an image_gen.py invocation. Never raises; on error returns args unchanged."""
    notes: list[str] = []
    new_args = [str(a) for a in args]
    try:
        new_args, cli_notes = sanitize_cli_model(new_args)
        notes += cli_notes
    except Exception:
        new_args = [str(a) for a in args]
    try:
        mp = _manifest_arg(new_args)
        if mp:
            notes += sanitize_manifest(resolve(mp))
    except Exception:
        pass  # malformed/missing manifest: let image_gen.py report its own error
    return new_args, notes


def image_models_block() -> str:
    """Compact IMAGE MODELS section for the agent's per-job message (built from env at call time)."""
    dflt = default_model()
    alts = [m for m in allowed_models() if m != dflt]
    lines = [f"IMAGE MODELS (image_gen.py): default = {dflt}."]
    if alts:
        lines.append("Allowed alternates: " + "; ".join(f"{m} ({_NOTES.get(m, 'alternate model')})" for m in alts) + ".")
    lines.append(
        "Leave `model` unset in image_prompts.json items to use the default. Only set a per-item `model` when there is a clear "
        "reason, using ONLY the allowed names; anything else is ignored."
    )
    return "\n".join(lines) + "\n"
