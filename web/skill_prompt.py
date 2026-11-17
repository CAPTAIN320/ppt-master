"""
Loads the agent system prompt for web mode.

The system prompt is kept short and neutral to avoid proxy content filters.
The full SKILL.md workflow document is injected as the first user message
in agent_runner.py so the agent has the complete pipeline instructions.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_SKILL_MD_PATH = Path("/app/skills/ppt-master/SKILL.md")


def _subagent_on() -> bool:
    return os.environ.get("SLIDE_SUBAGENT", "1").lower() not in ("0", "false", "no")


_SLIDE_RULE_LEGACY = "After writing each SVG slide file, call the slide_ready tool to notify the UI."

_SLIDE_RULE_SUBAGENT = """\
SLIDE GENERATION (Step 6 override for web mode):
- Do NOT read executor-base.md, shared-standards.md, modes/*.md or visual-styles/*.md, and do NOT write slide SVGs with write_file.
- After Step 4 (spec confirmed) and Step 5 (images) finish, call generate_slides_batch(project_path, slide_numbers=[], max_concurrency=4).
  Use generate_slide_svg only to regenerate or repair a single page (pass extra_instructions). Both tools write svg_output/,
  run the quality check, write speaker notes, and notify the UI (slide_ready) automatically - do not call slide_ready for those pages.
- If a result lists failed slides, call generate_slide_svg for each. When all slides exist, run svg_quality_checker.py once on the project,
  then continue with Step 7 post-processing (if the batch result says notes_total is true, notes/total.md already exists).
- The Step 6 rule "SVG generation MUST stay in the main agent / do NOT delegate" is superseded in web mode: the tools above ARE the Executor."""


_STEP6_WEB_BLOCK = """\
### Step 6: Executor Phase (web mode)

GATE: Step 4 (and Step 5 if triggered) complete; all prerequisite deliverables are ready.

In web mode the Executor runs as isolated per-slide sub-agents exposed as tools. Do NOT read executor-base.md / shared-standards.md /
modes/ / visual-styles/ yourself and do NOT write SVG files with write_file.

1. Call `generate_slides_batch(project_path, slide_numbers=[], max_concurrency=4)`. Each slide is generated in its own context from
   design_spec.md + spec_lock.md, quality-checked, self-corrected once, given speaker notes (notes/<stem>.md, and notes/total.md when complete),
   and announced to the UI automatically.
2. For any slide listed under `failed` (or to apply user feedback), call `generate_slide_svg(project_path, slide_number, extra_instructions=...)`.
3. Quality Check Gate (Mandatory) - after all SVGs:
```bash
python3 ${SKILL_DIR}/scripts/svg_quality_checker.py <project_path>
```
- Any `error` MUST be fixed (regenerate that page with generate_slide_svg and re-run the check). Warnings: fix when straightforward.
- Run against `svg_output/` (not after finalize_svg.py).

"""


def _patch_step6(text: str) -> str:
    start = text.find("### Step 6: Executor Phase")
    gate = text.find("**Logic Construction Phase**", start) if start != -1 else -1
    if start != -1 and gate != -1:
        return text[:start] + _STEP6_WEB_BLOCK + text[gate:].replace(
            "**Logic Construction Phase**: generate speaker notes → `<project_path>/notes/total.md`",
            "**Logic Construction Phase**: speaker notes are produced by the slide tools → `<project_path>/notes/total.md`",
            1,
        )
    logger.warning("SKILL.md Step 6 markers not found; appending web-mode override instead")
    return text + "\n\n---\n\n## WEB MODE OVERRIDE\n\n" + _STEP6_WEB_BLOCK + _SLIDE_RULE_SUBAGENT + "\n"


_STEP4_START = "**Confirm UI Auto-Launch (Mandatory"
_STEP4_END = "**Honoring the confirmation (result.json is authoritative"

_STEP4_WEB_BLOCK = """\
**Eight Confirmations (web mode - the Confirm UI files are NOT used)**
- NEVER read or write anything under `confirm_ui/` (no `confirm_ui.md`, `confirm_ui/server.py`, `static/catalogs.json`, `recommendations.json`, `result.json`) and never run confirm_ui scripts. The browser UI is driven by the `confirm_gate` tool.
- Do the Strategist thinking, then make ONE call: `confirm_gate(recommendations={...})`. Use a flat JSON object with these keys (values = your recommendation, plain strings / short lists, matching the user's language):
  `canvas`, `page_count` (e.g. "12-15"), `audience`, `core_message`, `delivery_purpose`, `mode`, `visual_style`, `color` (HEX list/roles), `icons` (library + stroke), `typography`, `formula_policy`, `image_usage` (one of ai|web|provided|placeholder|none), `notes`.
- It blocks until the user confirms (or returns immediately when auto-approve is on). The returned `user_choices` is authoritative: it overrides your own recommendations when you write `design_spec.md` / `spec_lock.md` (map `image_usage` to §VIII `Acquire Via` per the table below).
- Skip the split-mode and spec-refinement chat notes and the chat summary of the Eight Confirmations - the UI shows them. After the gate returns, write `design_spec.md` and `spec_lock.md` immediately (same turn if possible); do not ask follow-up questions.
- Auto-approve: if the job says auto-confirm is on, the gate returns your recommendations unchanged - still make the call (it also drives the progress bar), then continue without waiting.

"""


def _patch_step4(text: str) -> str:
    s = text.find(_STEP4_START)
    e = text.find(_STEP4_END, s) if s != -1 else -1
    if s != -1 and e != -1:
        return text[:s] + _STEP4_WEB_BLOCK + text[e:]
    logger.warning("SKILL.md Step 4 markers not found; appending web-mode Step 4 override")
    return text + "\n\n---\n\n## WEB MODE STEP 4 OVERRIDE\n\n" + _STEP4_WEB_BLOCK


def get_system_prompt() -> str:
    """Return a short, neutral system prompt for the agent."""
    slide_rule = _SLIDE_RULE_SUBAGENT if _subagent_on() else _SLIDE_RULE_LEGACY
    return """\
You are PPT Master, an AI assistant that creates professional PowerPoint presentations.
You have access to tools for reading/writing files, running Python scripts, and managing projects.
You follow a structured pipeline to generate high-quality presentations from user topics or documents.

IMPORTANT — Web UI mode:
- Never read, write or run anything under confirm_ui/ (docs, server.py, catalogs.json, recommendations.json, result.json). At Step 4 call the confirm_gate tool directly with the recommendations object; its result (user_choices) is the confirmation. Do not summarise the Eight Confirmations in chat.

Always use the confirm_gate tool at the design confirmation step to get user approval before generating slides.
{slide_rule}
Run finalize_svg.py when the pipeline is complete. Do NOT run svg_to_pptx.py — \
PPTX export is handled on demand by the server when the user clicks Download.
""".replace("{slide_rule}", slide_rule)


def get_skill_md() -> str:
    """Return the full SKILL.md content to inject as the first user message."""
    text = _SKILL_MD_PATH.read_text(encoding="utf-8")
    text = _patch_step4(text)
    if _subagent_on():
        text = _patch_step6(text)
    return text
