"""
Isolated per-slide sub-agent (Step 6 "Executor") for web mode.

Each slide is produced by a fresh, tool-less LLM call whose context is built
server-side from ``design_spec.md`` / ``spec_lock.md`` / reference sections /
style anchors.  The main agent's history only ever sees a tiny status dict.

Public entry points (dispatched from ``web.tools``):
  * ``generate_slide_svg(args, *, job_id, store, ctx)``
  * ``generate_slides_batch(args, *, job_id, store, ctx)``
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .llm_utils import LLMCancelled, _max_tokens_for, call_llm_text

REPO_ROOT = Path(os.environ.get("PPT_MASTER_ROOT", "/app"))
PROJECTS_DIR = REPO_ROOT / "projects"
SKILL_DIR = REPO_ROOT / "skills" / "ppt-master"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

MAX_PROMPT_CHARS = 180_000
MAX_DESIGN_SPEC_CHARS = 40_000

# ─────────────────────────────────────────────────────────────────────────────
# Markdown helpers
# ─────────────────────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError, OSError):
        return ""


def _split_sections(text: str, prefix: str = "## ") -> list[tuple[str, str]]:
    """Split markdown on headings starting with ``prefix`` → [(heading, body incl. heading)]."""
    out: list[tuple[str, str]] = []
    cur_head: str | None = None
    buf: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith(prefix) and not line.startswith(prefix + "#"):
            if cur_head is not None or buf:
                out.append((cur_head or "", "".join(buf)))
            cur_head = line.strip()
            buf = [line]
        else:
            buf.append(line)
    if cur_head is not None or buf:
        out.append((cur_head or "", "".join(buf)))
    return out


def _pick_sections(text: str, numbers: set[int]) -> str:
    """Pick '## N. ...' sections by number from a reference doc."""
    picked: list[str] = []
    for head, body in _split_sections(text):
        m = re.match(r"##\s+(\d+)\.", head)
        if m and int(m.group(1)) in numbers:
            picked.append(body.rstrip() + "\n")
    return "\n".join(picked)


# ─────────────────────────────────────────────────────────────────────────────
# Outline / spec_lock parsing
# ─────────────────────────────────────────────────────────────────────────────

_SLIDE_HEAD_RE = re.compile(
    r"^(#{3,5})\s+(?:Slide|Page|P|页|第)\s*0*(\d+)\s*(?:页)?\s*[-–—:：.、]?\s*(.*?)\s*$",
    re.IGNORECASE,
)
_ANY_HEAD_RE = re.compile(r"^#{1,5}\s")


@dataclass
class SlideBrief:
    number: int
    name: str
    text: str
    title: str = ""
    core_message: str = ""
    layout: str = ""
    visualization: str = ""


def _field(block: str, label: str) -> str:
    m = re.search(rf"^\s*[-*]\s*\*\*{label}\*\*\s*[:：]\s*(.+)$", block, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else ""


def parse_outline(design_spec_text: str) -> dict[int, SlideBrief]:
    """Parse ``#### Slide NN - Name`` blocks (also ``### Slide``/``#### Page``)."""
    lines = design_spec_text.splitlines()
    heads: list[tuple[int, int, str]] = []  # (line_idx, number, name)
    for i, line in enumerate(lines):
        m = _SLIDE_HEAD_RE.match(line)
        if m:
            heads.append((i, int(m.group(2)), m.group(3).strip()))
    outline: dict[int, SlideBrief] = {}
    for k, (idx, num, name) in enumerate(heads):
        end = len(lines)
        for j in range(idx + 1, len(lines)):
            if _ANY_HEAD_RE.match(lines[j]) and (not lines[j].startswith("#####") or _SLIDE_HEAD_RE.match(lines[j])):
                end = j
                break
        block_lines = lines[idx:end]
        while block_lines and block_lines[-1].strip() in ("", "---"):
            block_lines.pop()
        block = "\n".join(block_lines)
        if num in outline:  # keep first occurrence
            continue
        outline[num] = SlideBrief(
            number=num,
            name=name,
            text=block,
            title=_field(block, "Title"),
            core_message=_field(block, "Core message"),
            layout=_field(block, "Layout"),
            visualization=_field(block, "Visualization"),
        )
    return outline


def _parse_lock(text: str) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    cur: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            cur = line[3:].strip()
            sections.setdefault(cur, {})
            continue
        if cur is None:
            continue
        m = re.match(r"^-\s+([A-Za-z0-9_\-]+)\s*:\s*(.+?)\s*$", line)
        if m:
            sections[cur][m.group(1)] = m.group(2)
    return sections


def _flat_value(text: str, key: str) -> str:
    m = re.search(rf"^\s*-\s*{re.escape(key)}\s*:\s*(.+?)\s*$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def read_spec_lock(project_path: str | Path) -> tuple[str, dict[str, Any]]:
    """Return ``(raw_text, info)`` with mode, visual_style, viewBox and per-page maps."""
    raw = _read(Path(project_path) / "spec_lock.md")
    sections = _parse_lock(raw)
    info: dict[str, Any] = {
        "mode": _flat_value(raw, "mode"),
        "visual_style": _flat_value(raw, "visual_style"),
        "viewBox": sections.get("canvas", {}).get("viewBox", "") or _flat_value(raw, "viewBox"),
        "page_layouts": sections.get("page_layouts", {}),
        "page_charts": sections.get("page_charts", {}),
        "page_rhythm": sections.get("page_rhythm", {}),
        "images": sections.get("images", {}),
    }
    return raw, info


def _safe_token(value: str) -> str:
    value = (value or "").strip().strip("`'\"").lower()
    return value if re.fullmatch(r"[a-z0-9_\-]+", value) else ""


# ─────────────────────────────────────────────────────────────────────────────
# Naming
# ─────────────────────────────────────────────────────────────────────────────


def slugify(name: str, max_len: int = 40) -> str:
    s = re.sub(r"[^\w]+", "_", (name or "").strip().lower(), flags=re.UNICODE).strip("_")
    return s[:max_len].strip("_") or "slide"


def svg_filename(number: int, brief: SlideBrief | None, override: str | None, svg_dir: Path | None = None) -> str:
    if override:
        name = Path(override).name
        if not name.lower().endswith(".svg"):
            name += ".svg"
        return name
    if svg_dir is not None and svg_dir.is_dir():
        existing = sorted(svg_dir.glob(f"{number:02d}_*.svg"))
        if existing:
            return existing[0].name
    return f"{number:02d}_{slugify(brief.name if brief else '')}.svg"


# ─────────────────────────────────────────────────────────────────────────────
# Context blocks
# ─────────────────────────────────────────────────────────────────────────────


def adjacent_context(outline: dict[int, SlideBrief], n: int) -> str:
    lines = []
    for label, k in (("Prev", n - 1), ("Next", n + 1)):
        b = outline.get(k)
        if b:
            lines.append(f"{label}: P{k:02d} '{b.title or b.name}'" + (f" — {b.core_message}" if b.core_message else ""))
    return "\n".join(lines)


def image_facts(project: Path, lock_info: dict[str, Any]) -> str:
    img_dir = project / "images"
    files = sorted(p.name for p in img_dir.iterdir() if p.is_file() and not p.name.startswith(".")) if img_dir.is_dir() else []
    for csv_path in (project / "analysis" / "image_analysis.csv", project / "image_analysis.csv"):
        text = _read(csv_path)
        if text:
            try:
                rows = list(csv.reader(io.StringIO(text)))
                body = "\n".join(" | ".join(r) for r in rows[:60])
            except Exception:
                body = text[:4000]
            return f"image_analysis.csv:\n{body[:4000]}\n\nFiles in images/: {', '.join(files) or '(none)'}"
    if not files:
        return "(no images in images/)"
    facts = []
    try:
        from PIL import Image  # type: ignore

        for fn in files[:40]:
            try:
                with Image.open(img_dir / fn) as im:
                    w, h = im.size
                    facts.append(f"- {fn}: {w}x{h} ({'landscape' if w >= h else 'portrait'}, alpha={'A' in im.getbands()})")
            except Exception:
                facts.append(f"- {fn}")
    except Exception:
        facts = [f"- {fn}" for fn in files[:40]]
    return "\n".join(facts)


_CHROME_TOKENS = ("header", "footer", "bg", "background", "decor", "nav", "logo", "pagenumber", "page-number", "page_number")


def _excerpt_svg(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    head = int(cap * 0.7)
    tail = cap - head - 30
    return text[:head] + "\n<!-- …truncated -->\n" + text[-tail:]


def style_anchors(project_path: str | Path, exclude_filename: str | None = None, max_n: int = 2, cap: int = 6000) -> list[tuple[str, str]]:
    """Up to ``max_n`` already-generated slides (cover + first content slide) as trimmed excerpts."""
    svg_dir = Path(project_path) / "svg_output"
    if not svg_dir.is_dir():
        return []
    files = [p for p in sorted(svg_dir.glob("*.svg")) if p.name != exclude_filename]
    out: list[tuple[str, str]] = []
    for p in files[:max_n]:
        t = _read(p)
        if t.strip():
            out.append((p.name, _excerpt_svg(t, cap)))
    return out


def _template_svg(project: Path, rel: str) -> str:
    for base in (project / "templates", SKILL_DIR / "templates"):
        t = _read(base / rel)
        if t:
            return t
    return ""


def _clean_ref(value: str) -> str:
    return re.sub(r"\s+#.*$", "", value.strip().strip("`'\"")).strip()


def _trim_design_spec(text: str, cap: int = MAX_DESIGN_SPEC_CHARS, drop_tail: bool = False) -> str:
    out = text
    if drop_tail or len(out) > cap:
        parts = _split_sections(out)
        kept = [b for h, b in parts if not re.match(r"##\s+(X|XI)\.", h)]
        out = "".join(kept)
    if len(out) > cap:
        out = out[:cap] + "\n\n<!-- design_spec.md truncated for size -->\n"
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Prompt assembly
# ─────────────────────────────────────────────────────────────────────────────


def _system_prompt(project: Path, lock_info: dict[str, Any], level: int) -> str:
    refs = SKILL_DIR / "references"
    exec_text = _read(refs / "executor-base.md")
    std_text = _read(refs / "shared-standards.md")
    has_layouts = bool(lock_info.get("page_layouts")) or (project / "templates").is_dir()

    exec_nums = {2, 3, 4, 6, 7}
    if has_layouts and level < 2:
        exec_nums.add(1)
    std_nums = {1, 2, 4, 6, 7}
    if level >= 1:
        std_nums.discard(7)
    if level >= 2:
        std_nums.discard(6)

    parts = [
        "You are the PPT Master Executor. You produce exactly ONE SVG slide per request. You have no tools; all inputs are inline. No chatter.",
        "# Executor Guidelines (executor-base.md, extracted)\n\n" + _pick_sections(exec_text, exec_nums),
        "# Shared SVG/PPT Technical Standards (shared-standards.md, extracted)\n\n" + _pick_sections(std_text, std_nums),
    ]
    mode = _safe_token(lock_info.get("mode", ""))
    if mode and mode != "custom":
        t = _read(refs / "modes" / f"{mode}.md")
        if t:
            parts.append(f"# Mode: {mode}\n\n{t}")
    style = _safe_token(lock_info.get("visual_style", ""))
    if style and style != "custom":
        t = _read(refs / "visual-styles" / f"{style}.md")
        if t:
            parts.append(f"# Visual style: {style}\n\n{t}")
    parts.append(_read(PROMPTS_DIR / "slide_web_addendum.md"))
    return "\n\n---\n\n".join(p for p in parts if p.strip())


def build_slide_messages(
    project_path: str | Path,
    slide_number: int,
    filename: str | None = None,
    extra_instructions: str = "",
    with_notes: bool = True,
    confirm_result: Any = None,
    *,
    slide_spec: str | None = None,
) -> list[dict[str, str]]:
    """Build the ``[system, user]`` messages for one slide (with the size guard)."""
    project = Path(project_path)
    design_spec = _read(project / "design_spec.md")
    lock_raw, lock_info = read_spec_lock(project)
    outline = parse_outline(design_spec)
    brief = outline.get(slide_number)
    svg_dir = project / "svg_output"
    fname = filename or svg_filename(slide_number, brief, None, svg_dir)
    page_key = f"P{slide_number:02d}"

    brief_text = brief.text if brief else (slide_spec or "")
    if not brief_text:
        brief_text = f"(No '#### Slide {slide_number:02d}' block found in design_spec.md §IX — derive this page from the design spec and the instructions below.)"

    layout_ref = _clean_ref(lock_info["page_layouts"].get(page_key, "")) if lock_info.get("page_layouts") else ""
    chart_ref = _clean_ref(lock_info["page_charts"].get(page_key, "")) if lock_info.get("page_charts") else ""
    rhythm = _clean_ref(lock_info["page_rhythm"].get(page_key, "")) if lock_info.get("page_rhythm") else ""

    layout_svg = ""
    if layout_ref:
        base = layout_ref[:-4] if layout_ref.lower().endswith(".svg") else layout_ref
        layout_svg = _template_svg(project, f"{Path(base).name}.svg")
    chart_svg = ""
    if chart_ref:
        base = chart_ref[:-4] if chart_ref.lower().endswith(".svg") else chart_ref
        chart_svg = _template_svg(project, f"charts/{Path(base).name}.svg")

    this_page = [f"<this_page>", f"slide_number: {slide_number}", f"filename: {fname}", f"page_key: {page_key}"]
    if rhythm:
        this_page.append(f"page_rhythm: {rhythm}")
    if layout_ref:
        this_page.append(f"page_layout_template: {layout_ref}")
    if chart_ref:
        this_page.append(f"page_chart_template: {chart_ref}")
    this_page.append(f"with_notes: {'true' if with_notes else 'false'}")
    this_page.append("\n## Page brief (design_spec.md §IX)\n" + brief_text)
    adj = adjacent_context(outline, slide_number)
    if adj:
        this_page.append("\n## Adjacent pages\n" + adj)
    if layout_svg:
        this_page.append(f"\n## Layout template ({layout_ref}.svg)\n```svg\n{layout_svg}\n```")
    if chart_svg:
        this_page.append(f"\n## Chart template ({chart_ref}.svg)\n```svg\n{chart_svg}\n```")
    if extra_instructions:
        this_page.append("\n## Extra instructions for this slide\n" + extra_instructions[:2000])
    this_page.append("</this_page>")
    this_page_text = "\n".join(this_page)

    confirm_text = ""
    if confirm_result:
        try:
            confirm_text = "<user_confirmations>\n" + json.dumps(confirm_result, ensure_ascii=False)[:2000] + "\n</user_confirmations>\n\n"
        except Exception:
            confirm_text = ""

    anchors_all = style_anchors(project, fname, max_n=2, cap=6000)

    def _assemble(level: int) -> list[dict[str, str]]:
        spec = _trim_design_spec(design_spec, drop_tail=level >= 1)
        anchors = anchors_all if level < 3 else [(n, _excerpt_svg(t, 2500)) for n, t in anchors_all]
        anchor_text = ""
        if anchors:
            anchor_text = "<style_anchors>\nPreviously generated slides of this deck — match their chrome, spacing and typographic scale.\n" + "\n".join(
                f"\n### {n}\n```svg\n{t}\n```" for n, t in anchors
            ) + "\n</style_anchors>\n\n"
        user = (
            f"<spec_lock.md>\n{lock_raw}\n</spec_lock.md>\n\n"
            f"<design_spec.md>\n{spec}\n</design_spec.md>\n\n"
            f"<image_facts>\n{image_facts(project, lock_info)}\n</image_facts>\n\n"
            f"{confirm_text}{anchor_text}{this_page_text}\n\n"
            f"Generate slide {slide_number} now. Reply with the <svg> element"
            + (" followed by <notes>." if with_notes else " only.")
        )
        return [{"role": "system", "content": _system_prompt(project, lock_info, level)}, {"role": "user", "content": user}]

    msgs = _assemble(0)
    level = 0
    while sum(len(m["content"]) for m in msgs) > MAX_PROMPT_CHARS and level < 3:
        level += 1
        msgs = _assemble(level)
    return msgs


# ─────────────────────────────────────────────────────────────────────────────
# Extraction & checks
# ─────────────────────────────────────────────────────────────────────────────


def extract_svg_notes(text: str) -> tuple[str | None, str | None]:
    """Return ``(svg, notes)`` from raw model output (either may be None)."""
    cleaned = re.sub(r"```[a-zA-Z]*\n?", "", text or "")
    start = cleaned.find("<svg")
    end = cleaned.rfind("</svg>")
    svg = cleaned[start : end + len("</svg>")].strip() if start != -1 and end != -1 and end > start else None
    after = cleaned[end + len("</svg>") :] if svg is not None else cleaned
    notes = None
    m = re.search(r"<notes>([\s\S]*?)(?:</notes>|$)", after)
    if m:
        notes = m.group(1).strip() or None
    return svg, notes


def _run_quality_check(svg_path: Path) -> dict[str, Any]:
    try:
        scripts = str(SKILL_DIR / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from svg_quality_checker import SVGQualityChecker  # type: ignore

        res = SVGQualityChecker().check_file(str(svg_path))
        return {"errors": list(res.get("errors", [])), "warnings": list(res.get("warnings", [])), "checked": True}
    except Exception as exc:  # checker unavailable → don't block the pipeline
        return {"errors": [], "warnings": [f"quality checker unavailable: {exc}"[:160]], "checked": False}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def assemble_notes_total(project: Path) -> bool:
    """Write notes/total.md when every SVG in svg_output/ has a notes file."""
    svg_dir = project / "svg_output"
    notes_dir = project / "notes"
    svgs = sorted(svg_dir.glob("*.svg")) if svg_dir.is_dir() else []
    if not svgs:
        return False
    chunks = []
    for s in svgs:
        body = _read(notes_dir / f"{s.stem}.md").strip()
        if not body:
            return False
        chunks.append(f"# {s.stem}\n\n{body}\n\n---\n")
    _atomic_write(notes_dir / "total.md", "\n".join(chunks))
    return True


def _resolve_project(raw: Any) -> Path | None:
    if not raw or not isinstance(raw, str):
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = REPO_ROOT / p
    try:
        rp = p.resolve()
        if rp.is_relative_to(PROJECTS_DIR.resolve()) and rp != PROJECTS_DIR.resolve():
            return rp
    except Exception:
        pass
    return None


def _get_semaphore(ctx: Any) -> asyncio.Semaphore:
    sem = getattr(ctx, "slide_semaphore", None)
    if sem is None:
        try:
            n = int(os.environ.get("SLIDE_SUBAGENT_CONCURRENCY", "3"))
        except ValueError:
            n = 3
        sem = asyncio.Semaphore(max(1, n))
        ctx.slide_semaphore = sem
    return sem


def _slide_model(ctx: Any) -> str:
    return getattr(ctx, "slide_model", None) or ctx.model


def _short(items: list[str], n: int = 3, width: int = 160) -> list[str]:
    return [str(i)[:width] for i in items[:n]]


# ─────────────────────────────────────────────────────────────────────────────
# Single slide
# ─────────────────────────────────────────────────────────────────────────────


async def _llm(ctx: Any, messages: list[dict[str, str]], *, job_id: str, store: Any, label: str, max_tokens: int | None = None) -> tuple[str, str | None]:
    model = _slide_model(ctx)
    async with _get_semaphore(ctx):
        return await call_llm_text(ctx.client, model, messages, max_tokens=max_tokens or _max_tokens_for(model), job_id=job_id, store=store, label=label)


async def _generate_one(args: dict[str, Any], *, job_id: str, store: Any, ctx: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Generate one slide. Returns ``(status_dict, slide_ready_event | None)``."""
    t0 = time.time()
    project = _resolve_project(args.get("project_path"))
    if project is None:
        return {"status": "failed", "error": "project_path must be inside /app/projects"}, None
    if not (project / "design_spec.md").is_file() or not (project / "spec_lock.md").is_file():
        return {"status": "failed", "error": "design_spec.md/spec_lock.md missing - finish Step 4 first"}, None
    try:
        n = int(args.get("slide_number"))
    except (TypeError, ValueError):
        return {"status": "failed", "error": "slide_number (integer) required"}, None
    if n < 1:
        return {"status": "failed", "error": "slide_number must be >= 1"}, None

    with_notes = args.get("with_notes", True) is not False
    extra = args.get("extra_instructions") or ""
    if not isinstance(extra, str):
        extra = json.dumps(extra, ensure_ascii=False)
    slide_spec = args.get("slide_spec") or (f"Title: {args['title']}" if args.get("title") else None)
    label = f"Slide {n:02d}"

    if store.is_cancelled(job_id):
        return {"status": "cancelled", "slide_number": n}, None

    design_spec = await asyncio.to_thread(_read, project / "design_spec.md")
    outline = parse_outline(design_spec)
    svg_dir = project / "svg_output"
    filename = svg_filename(n, outline.get(n), args.get("filename"), svg_dir)
    svg_path = svg_dir / filename
    existed_before = svg_path.is_file()

    try:
        confirm_result = store.get_confirm_result(job_id)
    except Exception:
        confirm_result = None

    messages = await asyncio.to_thread(
        build_slide_messages, project, n, filename, extra, with_notes, confirm_result, slide_spec=slide_spec
    )
    prompt_chars = sum(len(m["content"]) for m in messages)
    await store.append_log(job_id, f"[{label}] generating (model={_slide_model(ctx)}, prompt≈{prompt_chars // 1000}K chars)\n")

    try:
        text, finish = await _llm(ctx, messages, job_id=job_id, store=store, label=label)
        svg, notes = extract_svg_notes(text)
        # Truncation recovery: 1 continuation, then 1 compact retry
        if finish == "length" or svg is None:
            if "</svg>" not in text:
                await store.append_log(job_id, f"[{label}] output truncated/unclosed; continuing\n")
                cont_msgs = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": "Continue EXACTLY where you stopped; output only the remaining characters (no repetition, no fences)."},
                ]
                more, finish2 = await _llm(ctx, cont_msgs, job_id=job_id, store=store, label=label)
                text = text + more
                svg, notes = extract_svg_notes(text)
                if svg is None:
                    await store.append_log(job_id, f"[{label}] still unclosed; compact retry\n")
                    retry_msgs = [dict(m) for m in messages]
                    retry_msgs[-1]["content"] += "\n\nBe more compact: keep the SVG under 12 KB with fewer decorative elements. Close every tag."
                    text, finish3 = await _llm(ctx, retry_msgs, job_id=job_id, store=store, label=label, max_tokens=16000)
                    svg, notes = extract_svg_notes(text)
                    if svg is None:
                        return {"status": "failed", "slide_number": n, "error": f"truncated twice (finish_reason={finish3})"}, None
            elif svg is None:
                return {"status": "failed", "slide_number": n, "error": "no complete <svg> element in model output"}, None
    except LLMCancelled:
        return {"status": "cancelled", "slide_number": n}, None
    except Exception as exc:
        return {"status": "failed", "slide_number": n, "error": f"LLM call failed: {str(exc)[:200]}"}, None

    if store.is_cancelled(job_id):
        return {"status": "cancelled", "slide_number": n}, None

    await asyncio.to_thread(_atomic_write, svg_path, svg)
    store.set_project_path(job_id, str(project))
    qc = await asyncio.to_thread(_run_quality_check, svg_path)
    corrected = False

    if qc["errors"] and not store.is_cancelled(job_id):
        await store.append_log(job_id, f"[{label}] {len(qc['errors'])} quality error(s); running one self-correction pass\n")
        fix_msgs = messages + [
            {"role": "assistant", "content": svg},
            {
                "role": "user",
                "content": "The SVG failed the quality check. Fix ONLY these errors and return the full corrected <svg>...</svg> (no notes, no commentary):\n"
                + "\n".join(f"- {e}" for e in qc["errors"][:20]),
            },
        ]
        try:
            fixed_text, _ = await _llm(ctx, fix_msgs, job_id=job_id, store=store, label=label)
            fixed_svg, _n = extract_svg_notes(fixed_text)
            if fixed_svg:
                backup = svg
                await asyncio.to_thread(_atomic_write, svg_path, fixed_svg)
                qc2 = await asyncio.to_thread(_run_quality_check, svg_path)
                corrected = True
                if len(qc2["errors"]) <= len(qc["errors"]):
                    qc = qc2
                    svg = fixed_svg
                else:
                    await asyncio.to_thread(_atomic_write, svg_path, backup)
        except LLMCancelled:
            return {"status": "cancelled", "slide_number": n}, None
        except Exception as exc:
            await store.append_log(job_id, f"[{label}] self-correction failed: {str(exc)[:160]}\n")

    wrote_notes = False
    if with_notes and notes:
        await asyncio.to_thread(_atomic_write, project / "notes" / f"{svg_path.stem}.md", notes + "\n")
        wrote_notes = True

    event = {
        "type": "slide_ready",
        "slide": n,
        "url": f"/jobs/{job_id}/slides/{n}",
        "svg_path": str(svg_path),
        **({"regenerated": True} if existed_before else {}),
    }
    status = {
        "status": "ok" if not qc["errors"] else "ok_with_errors",
        "slide_number": n,
        "svg_path": str(svg_path),
        "bytes": len(svg.encode("utf-8")),
        "quality": {"errors": len(qc["errors"]), "warnings": len(qc["warnings"])},
        "corrected": corrected,
        "notes": wrote_notes,
    }
    if qc["errors"]:
        status["errors"] = _short(qc["errors"])
    await store.append_log(
        job_id,
        f"[{label}] done {status['bytes'] / 1024:.1f}KB, {len(qc['errors'])} errors / {len(qc['warnings'])} warnings, {time.time() - t0:.1f}s\n",
    )
    return status, event


async def generate_slide_svg(args: dict[str, Any], *, job_id: str, store: Any, ctx: Any) -> dict[str, Any]:
    status, event = await _generate_one(args, job_id=job_id, store=store, ctx=ctx)
    if event is not None:
        await store.push_event(job_id, event)
        project = _resolve_project(args.get("project_path"))
        if project is not None:
            try:
                total = await asyncio.to_thread(assemble_notes_total, project)
                status["notes_total"] = bool(total)
            except Exception:
                pass
    return status


# ─────────────────────────────────────────────────────────────────────────────
# Batch
# ─────────────────────────────────────────────────────────────────────────────


class _OrderedEmitter:
    """Emit slide_ready events in ascending slide order as contiguous slides complete."""

    def __init__(self, order: list[int], job_id: str, store: Any):
        self.order = order
        self.job_id = job_id
        self.store = store
        self.done: dict[int, dict[str, Any] | None] = {}
        self.ptr = 0

    async def complete(self, n: int, event: dict[str, Any] | None) -> None:
        self.done[n] = event
        while self.ptr < len(self.order) and self.order[self.ptr] in self.done:
            ev = self.done[self.order[self.ptr]]
            self.ptr += 1
            if ev is not None:
                await self.store.push_event(self.job_id, ev)

    async def flush(self) -> None:
        for n in self.order:
            if n in self.done and self.done[n] is not None and self.order.index(n) >= self.ptr:
                await self.store.push_event(self.job_id, self.done[n])
        self.ptr = len(self.order)


async def generate_slides_batch(args: dict[str, Any], *, job_id: str, store: Any, ctx: Any) -> dict[str, Any]:
    project = _resolve_project(args.get("project_path"))
    if project is None:
        return {"status": "failed", "error": "project_path must be inside /app/projects"}
    if not (project / "design_spec.md").is_file() or not (project / "spec_lock.md").is_file():
        return {"status": "failed", "error": "design_spec.md/spec_lock.md missing - finish Step 4 first"}

    outline = parse_outline(await asyncio.to_thread(_read, project / "design_spec.md"))
    raw_nums = args.get("slide_numbers", "all")
    if raw_nums == "all" or raw_nums is None:
        nums = sorted(outline)
        if not nums:
            return {"status": "failed", "error": "no '#### Slide NN' blocks found in design_spec.md §IX; pass explicit slide_numbers"}
    else:
        try:
            nums = sorted({int(x) for x in raw_nums})
        except (TypeError, ValueError):
            return {"status": "failed", "error": "slide_numbers must be 'all' or a list of integers"}

    try:
        max_conc = max(1, min(int(args.get("max_concurrency", 3) or 3), 6))
    except (TypeError, ValueError):
        max_conc = 3
    overwrite = bool(args.get("overwrite", False))
    with_notes = args.get("with_notes", True) is not False
    extra_map = args.get("extra_instructions") or {}
    if not isinstance(extra_map, dict):
        extra_map = {}

    svg_dir = project / "svg_output"

    def _exists(n: int) -> bool:
        return svg_dir.is_dir() and bool(list(svg_dir.glob(f"{n:02d}_*.svg")))

    skipped = [n for n in nums if not overwrite and _exists(n)]
    todo = [n for n in nums if n not in skipped]
    generated: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    cancelled = False
    emitter = _OrderedEmitter(todo, job_id, store)
    total = len(todo)
    finished = 0

    def _args_for(n: int) -> dict[str, Any]:
        return {
            "project_path": str(project),
            "slide_number": n,
            "with_notes": with_notes,
            "extra_instructions": extra_map.get(str(n)) or extra_map.get(n) or "",
        }

    async def _run(n: int) -> None:
        nonlocal finished, cancelled
        if store.is_cancelled(job_id):
            cancelled = True
            await emitter.complete(n, None)
            return
        try:
            status, event = await _generate_one(_args_for(n), job_id=job_id, store=store, ctx=ctx)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status, event = {"status": "failed", "slide_number": n, "error": str(exc)[:200]}, None
        finished += 1
        st = status.get("status")
        if st in ("ok", "ok_with_errors"):
            generated.append(
                {
                    "slide": n,
                    "file": Path(status["svg_path"]).name,
                    "bytes": status["bytes"],
                    "errors": status["quality"]["errors"],
                    "warnings": status["quality"]["warnings"],
                }
            )
        elif st == "cancelled":
            cancelled = True
        else:
            failed.append({"slide": n, "error": status.get("error", "unknown")})
        await emitter.complete(n, event)
        await store.append_log(job_id, f"[Slides] {finished}/{total} done\n")

    anchors_needed = max(0, 2 - len(list(svg_dir.glob("*.svg")))) if svg_dir.is_dir() else 2
    serial = todo[: min(anchors_needed, len(todo))]
    rest = todo[len(serial) :]

    for n in serial:
        await _run(n)
        if cancelled:
            break

    if rest and not cancelled:
        local = asyncio.Semaphore(max_conc)

        async def _bounded(n: int) -> None:
            async with local:
                await _run(n)

        tasks = [asyncio.ensure_future(_bounded(n)) for n in rest]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            raise

    await emitter.flush()
    notes_total = False
    try:
        notes_total = await asyncio.to_thread(assemble_notes_total, project)
    except Exception:
        pass
    if generated:
        store.set_project_path(job_id, str(project))

    generated.sort(key=lambda g: g["slide"])
    failed.sort(key=lambda g: g["slide"])
    if cancelled and not generated and not failed:
        overall = "cancelled"
    elif failed or cancelled:
        overall = "partial" if generated else ("cancelled" if cancelled else "failed")
    else:
        overall = "ok"
    return {
        "status": overall,
        "generated": generated,
        "failed": failed,
        "skipped": skipped,
        "quality_totals": {
            "errors": sum(g["errors"] for g in generated),
            "warnings": sum(g["warnings"] for g in generated),
        },
        "notes_total": bool(notes_total),
    }
