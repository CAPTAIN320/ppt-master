# SliderAI × PPT Master Integration Plan

This document outlines how the [SliderAI](../sliderai/) project can integrate
[PPT Master](.) to add natively editable `.pptx` export to its existing
web-based presentation generation pipeline.

---

## Background

### What SliderAI produces today

SliderAI is a full-stack SaaS (SvelteKit + NestJS + FastAPI + Postgres + S3)
that generates presentations as **structured JSON stored in a database**,
rendered as a web-based viewer in the browser. It does **not** produce `.pptx`
files.

```
User → SvelteKit (5173) → NestJS (4141) → LiteLLM/OpenAI → slide JSON
                                        → Gemini/fal.ai  → image URLs (S3)
                                        → Prisma DB      → web viewer
```

**SliderAI's slide schema (per slide):**

```ts
{
  presentationId: string;
  slideNum: number;
  title: string; // slide title
  content: string; // 3-sentence body text
  image: string; // S3/CloudFront image URL
}
```

### What PPT Master adds

PPT Master converts content into **natively editable `.pptx` files** with real
DrawingML shapes, text boxes, charts, speaker notes, and animations — fully
clickable and editable in PowerPoint, Keynote, WPS, and LibreOffice.

| Capability             | SliderAI (now)                    | PPT Master                      |
| ---------------------- | --------------------------------- | ------------------------------- |
| Output format          | Web viewer (JSON → browser)       | Native `.pptx` (DrawingML)      |
| Editable in PowerPoint | ❌                                | ✅                              |
| Design quality         | Template-based, 3 sentences/slide | Full design spec, visual styles |
| Charts / diagrams      | Mermaid only                      | 60+ chart templates             |
| Speaker notes          | ❌                                | ✅                              |
| Audio narration        | ❌                                | ✅                              |
| Brand identity         | ❌                                | ✅                              |

---

## Integration Options

Three options are described below, ordered from least invasive to most
powerful. They are not mutually exclusive — Option 1 can ship first and
Options 2 and 3 can be layered on top.

---

## Option 1 — "Export to PPTX" Button

**Effort**: Low · **Risk**: Low · **Time to ship**: ~1–2 weeks

### What it does

Adds a single "Download as PowerPoint" button to the existing presentation
viewer. SliderAI's generation pipeline is **unchanged**. After a presentation
is generated normally, the user can request a `.pptx` export on demand.

### How it works

```
User clicks "Download as PPTX"
  → SvelteKit calls NestJS POST /api/v1/presentation/export-pptx
  → NestJS reads slide data from Prisma DB
  → NestJS calls Python server POST /export-pptx
  → Python server:
      1. Builds one SVG file per slide from title + content + image URL
      2. Runs finalize_svg.py  (post-processing)
      3. Runs svg_to_pptx.py   (export)
      4. Returns .pptx as binary or uploads to S3 and returns URL
  → NestJS returns download URL to SvelteKit
  → Browser downloads the file
```

### Files to create / modify

#### In `sliderai/python-server/`

Copy PPT Master's post-processing scripts into the existing Python container
(they share PyMuPDF already):

```
sliderai/python-server/
├── ppt_master/                  ← copy from ppt-master/skills/ppt-master/scripts/
│   ├── finalize_svg.py
│   ├── svg_to_pptx.py
│   └── (shared utilities)
├── export_pptx.py               ← NEW: SVG builder + orchestrator
└── app.py                       ← ADD: /export-pptx endpoint
```

**`export_pptx.py`** — new file that:

1. Accepts slide JSON (title, content, image URL, slide count)
2. Generates one SVG per slide using a simple base template
3. Calls `finalize_svg.py` then `svg_to_pptx.py`
4. Returns the `.pptx` path or uploads to S3

**`app.py`** — add one new FastAPI route:

```python
@app.post("/export-pptx")
async def export_pptx(request: ExportRequest):
    pptx_path = await build_pptx_from_slides(request.slides, request.title)
    return FileResponse(pptx_path, media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
```

#### In `sliderai/server/`

Add one new NestJS endpoint in
`src/controllers/presentation.controller.ts`:

```ts
@Post('export-pptx')
async exportPptx(
  @Headers('Authorization') authorization: string,
  @Body('presentationId') presentationId: string,
  @Body('userEmail') userEmail: string,
) { ... }
```

And a corresponding method in
`src/services/presentation.service.ts` that:

1. Fetches slides from Prisma by `presentationId`
2. POSTs to the Python server `/export-pptx`
3. Returns the download URL

#### In `sliderai/web/`

Add a "Download as PowerPoint" button to the presentation viewer page
(`src/routes/presentation/[presentation]/+page.svelte`).

### SVG template approach

For Option 1, each slide SVG uses a **fixed base layout** (no AI design spec):

```
┌─────────────────────────────────────────┐
│  [Slide Title]                          │
│─────────────────────────────────────────│
│  [Image]          [Content text]        │
│                                         │
└─────────────────────────────────────────┘
```

This is intentionally simple — the goal is editable `.pptx` output, not
design quality. Design quality is addressed in Options 2 and 3.

### Dependencies to add to `python-server/requirements.txt`

```
python-pptx>=0.6.21
svglib>=1.5.0
reportlab>=4.0.0
```

---

## Option 2 — Parallel "Generate as PowerPoint" Mode

**Effort**: Medium · **Risk**: Medium · **Time to ship**: ~3–5 weeks

### What it does

Adds a "Generate as PowerPoint" toggle in the generation UI. When selected,
SliderAI's LLM still generates slide content (JSON) as normal, but instead of
only storing it in the DB for the web viewer, a PPT Master worker also
produces a high-quality `.pptx` with proper design spec, visual styles, and
layout templates.

### How it works

```
User selects "Generate as PowerPoint" + submits prompt
  → NestJS runs existing generation pipeline (LiteLLM → slide JSON)
  → NestJS also calls new PPT Master Worker service:
      1. Formats slide JSON as design_spec.md
      2. Generates SVGs (one per slide) using PPT Master layout templates
      3. Runs finalize_svg.py → svg_to_pptx.py
      4. Uploads .pptx to S3
      5. Stores S3 URL in Prisma (new `pptxUrl` field on Presentation)
  → Web viewer shows both "View online" and "Download .pptx" options
```

### Architecture changes

```
sliderai/
├── docker-compose.yaml          ← ADD ppt-master-worker service
├── server/
│   ├── app/mainApp.ts           ← ADD pptx generation branch
│   └── prisma/schema.prisma     ← ADD pptxUrl field to Presentation model
└── web/
    └── src/routes/presentation/ ← ADD download button + pptx status polling
```

### New `ppt-master-worker` service

A new lightweight FastAPI service (separate container) that:

- Accepts `POST /generate` with `{ title, slides[], language, imageModel, designStyle }`
- Writes a `design_spec.md` from the slide data
- Generates SVGs using PPT Master's chart and layout templates
- Runs the post-processing pipeline
- Returns a `.pptx` S3 URL

```yaml
# docker-compose.yaml addition
ppt-master-worker:
  build:
    context: ../ppt-master
    dockerfile: Dockerfile.worker # new file
  container_name: sliderai-ppt-master
  ports:
    - 4343:3003
  environment:
    PORT: 3003
    IMAGE_BACKEND: openai
    OPENAI_API_KEY: "${OPENAI_API_KEY}"
    ACCESS_KEY_ID_AWS: "${ACCESS_KEY_ID_AWS}"
    SECRET_ACCESS_KEY_AWS: "${SECRET_ACCESS_KEY_AWS}"
    BUCKET_NAME_AWS: "${BUCKET_NAME_AWS}"
    CLOUDFRONT_URL_AWS: "${CLOUDFRONT_URL_AWS}"
  restart: always
```

### Design spec generation

The worker converts SliderAI's slide JSON into a PPT Master `design_spec.md`:

```python
def slides_to_design_spec(title: str, slides: list, style: str = "swiss-minimal") -> str:
    """Convert SliderAI slide JSON to PPT Master design_spec.md format."""
    ...
```

This uses PPT Master's existing layout templates from
`skills/ppt-master/templates/layouts/` and chart templates from
`skills/ppt-master/templates/charts/` — no AI agent required for the
post-processing step.

### Prisma schema change

```prisma
model Presentation {
  // ... existing fields ...
  pptxUrl      String?   // S3 URL of exported .pptx (null until ready)
  pptxStatus   String?   // "generating" | "ready" | "failed"
}
```

---

## Option 3 — Full PPT Master Pipeline as a Managed Service

**Effort**: High · **Risk**: High · **Time to ship**: ~8–12 weeks

### What it does

Integrates the **complete** PPT Master pipeline — including the AI-driven SVG
generation step (Strategist → Image_Generator → Executor) — as a managed
background worker. This produces the highest-quality output: full design spec,
per-page visual styles, AI-generated images, speaker notes, and animations.

### How it works

```
User submits prompt + "Generate as PowerPoint (Premium)"
  → NestJS creates a job in a queue (Bull/Redis)
  → PPT Master Worker picks up the job:
      1. Calls Claude/GPT API with SKILL.md as system prompt
      2. AI agent runs the full pipeline:
         - Strategist: design_spec.md + spec_lock.md
         - Image_Generator: AI images via image_gen.py
         - Executor: SVG pages (one per slide, sequentially)
      3. Post-processing: finalize_svg.py → svg_to_pptx.py
      4. Uploads .pptx to S3
  → Prisma updated with pptxUrl + status
  → Web frontend polls status and shows download link when ready
```

### Architecture

```
sliderai/
├── docker-compose.yaml
│   ├── web              (existing)
│   ├── server           (existing)
│   ├── python-server    (existing)
│   ├── redis            ← NEW: job queue
│   └── ppt-master-worker ← NEW: full pipeline worker
└── server/
    ├── app/mainApp.ts   ← ADD: enqueue PPT Master job
    └── app/queue/       ← NEW: Bull queue setup
```

### The hard part — LLM agent loop

PPT Master's SVG generation is currently done by an AI coding agent (Claude
Code, Copilot, etc.) that reads files and runs shell commands interactively.
To run this headlessly in a container, you must build an **agent loop**:

```ts
// Pseudocode for the agent loop
async function runPptMasterPipeline(job: Job) {
  const systemPrompt = readFileSync("skills/ppt-master/SKILL.md", "utf8");
  const messages = [{ role: "system", content: systemPrompt }];

  // Tool definitions: read_file, write_file, execute_command
  const tools = [readFileTool, writeFileTool, executeCommandTool];

  // Agentic loop
  while (true) {
    const response = await claude.messages.create({
      model: "claude-opus-4-5",
      max_tokens: 8192,
      system: systemPrompt,
      messages,
      tools,
    });

    if (response.stop_reason === "end_turn") break;

    // Execute tool calls, append results, continue
    const toolResults = await executeTools(response.content);
    messages.push({ role: "assistant", content: response.content });
    messages.push({ role: "user", content: toolResults });
  }
}
```

This is essentially reimplementing what Claude Code does — non-trivial but
well-defined. The Anthropic tool-use API supports exactly this pattern.

### Queue setup (Bull + Redis)

```ts
// server/app/queue/pptmaster.queue.ts
import Bull from "bull";

export const pptMasterQueue = new Bull("ppt-master", {
  redis: { host: "redis", port: 6379 },
});

pptMasterQueue.process(async (job) => {
  await callPptMasterWorker(job.data);
});
```

### Cost and quality trade-off

|                    | Option 1               | Option 2                    | Option 3                     |
| ------------------ | ---------------------- | --------------------------- | ---------------------------- |
| Output quality     | Basic (fixed template) | Good (PPT Master templates) | Excellent (full AI pipeline) |
| LLM cost per deck  | $0 extra               | $0 extra                    | ~$0.50–$2.00 (Claude Opus)   |
| Generation time    | ~10s                   | ~30–60s                     | ~3–10 min                    |
| Engineering effort | Low                    | Medium                      | High                         |

---

## Recommended Rollout Order

```
Phase 1 (Week 1–2):   Option 1 — Export button on existing presentations
Phase 2 (Week 3–5):   Option 2 — Parallel PPTX generation mode
Phase 3 (Week 8–12):  Option 3 — Full AI pipeline (premium tier)
```

### Phase 1 deliverables

- [ ] Copy PPT Master post-processing scripts into `sliderai/python-server/`
- [ ] Write `export_pptx.py` SVG builder (fixed base template)
- [ ] Add `/export-pptx` FastAPI endpoint to `python-server/app.py`
- [ ] Add `POST /api/v1/presentation/export-pptx` to NestJS controller + service
- [ ] Add "Download as PowerPoint" button to presentation viewer
- [ ] Update `python-server/requirements.txt` with `python-pptx`, `svglib`, `reportlab`
- [ ] Test end-to-end with existing presentations

### Phase 2 deliverables

- [ ] Create `ppt-master-worker` FastAPI service with `Dockerfile.worker`
- [ ] Write `slides_to_design_spec()` converter
- [ ] Add `pptxUrl` + `pptxStatus` fields to Prisma `Presentation` model
- [ ] Add "Generate as PowerPoint" toggle to generation UI
- [ ] Add PPTX status polling to presentation viewer
- [ ] Add `ppt-master-worker` to `docker-compose.yaml`
- [ ] Wire NestJS `mainApp.ts` to call the worker after generation

### Phase 3 deliverables

- [ ] Add Redis + Bull queue to `docker-compose.yaml`
- [ ] Implement Claude tool-use agent loop in `ppt-master-worker`
- [ ] Add premium tier gating in SliderAI's credit system
- [ ] Add generation progress streaming to the web frontend
- [ ] End-to-end testing with full PPT Master pipeline

---

## Shared Dependencies

Both projects already share:

| Dependency       | SliderAI              | PPT Master                |
| ---------------- | --------------------- | ------------------------- |
| `PyMuPDF` (fitz) | ✅ `pymupdf==1.23.25` | ✅ `PyMuPDF>=1.23.0`      |
| `requests`       | ✅                    | ✅                        |
| `Pillow`         | ✅                    | ✅ (via image tools)      |
| AWS S3           | ✅ (images)           | ➕ (add for .pptx upload) |

New dependencies needed in `sliderai/python-server/`:

```
python-pptx>=0.6.21   # PPTX generation
svglib>=1.5.1         # SVG → PNG fallback for older Office
reportlab>=4.0.0      # Required by svglib
edge-tts>=7.2.8       # Optional: narration (Phase 3 only)
```

---

## Reference Files

| File                                                                                                                 | Purpose                           |
| -------------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| [`skills/ppt-master/SKILL.md`](skills/ppt-master/SKILL.md)                                                           | Authoritative PPT Master workflow |
| [`skills/ppt-master/scripts/svg_to_pptx.py`](skills/ppt-master/scripts/svg_to_pptx.py)                               | Core PPTX export script           |
| [`skills/ppt-master/scripts/finalize_svg.py`](skills/ppt-master/scripts/finalize_svg.py)                             | SVG post-processing               |
| [`skills/ppt-master/templates/charts/`](skills/ppt-master/templates/charts/)                                         | 60+ chart/diagram SVG templates   |
| [`skills/ppt-master/templates/layouts/`](skills/ppt-master/templates/layouts/)                                       | Page layout templates             |
| [`skills/ppt-master/references/shared-standards.md`](skills/ppt-master/references/shared-standards.md)               | SVG/PPTX technical constraints    |
| [`../sliderai/server/app/mainApp.ts`](../sliderai/server/app/mainApp.ts)                                             | SliderAI generation orchestrator  |
| [`../sliderai/server/src/services/presentation.service.ts`](../sliderai/server/src/services/presentation.service.ts) | SliderAI presentation service     |
| [`../sliderai/python-server/main.py`](../sliderai/python-server/main.py)                                             | SliderAI Python server (PDF/OCR)  |
| [`../sliderai/docker-compose.yaml`](../sliderai/docker-compose.yaml)                                                 | SliderAI container orchestration  |
