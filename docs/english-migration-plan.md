# English-Only Language Migration Plan

> Scope: PPT Master repository at `/Users/yaqub.mahmoud/github/ppt-master`.
> Status: Planning document — no code changes are made here.

---

## 1. Executive Summary

**Goal**: Make English the sole default generation language for all AI-produced output in PPT Master. After migration, a user who sends any generation request without specifying a language receives an English deck, English UI labels, English voice defaults, and English chart series names.

**What changes**:

- Generation output defaults (chart series names, filename examples in AI instructions, voice template defaults)
- Routing trigger phrases in `AGENTS.md` and `SKILL.md` (Chinese-only triggers become English-first, with Chinese retained as aliases)
- Confirm UI locale default (switches from `zh` to `en` on first load)
- `catalogs.json` default `label` fields (currently Chinese strings used as fallback)
- TTS voice description strings in `backend_edge.py`
- Chinese-named template directories (aliased via `decks_index.json` / `brands_index.json`, not deleted)
- Chinese-named workflow trigger examples in standalone workflow files

**What does NOT change**:

- Chinese regex patterns in source-ingestion parsers (`pdf_to_md.py`, `web_to_md.py`, `total_md_split.py`) — Chinese PDFs and web pages must remain ingestible
- Legacy spec filename constants in `project_utils.py` that detect old Chinese-named project files — removing them breaks validation of existing projects
- `docs/zh/` documentation files and `README_CN.md` — archived in place, not deleted
- Chinese-named template directories themselves — renamed only if a clean alias path is established; originals kept for backward compatibility
- The `zh` locale block in `app.js` — the bilingual toggle is a feature, not a bug; only the _default_ locale changes

---

## 2. Guiding Principles

1. **Generation output defaults to English.** Any AI-produced text that appears in a generated deck, chart, or audio file must default to English when no language is specified.
2. **Chinese source documents remain fully ingestible.** Parser regex patterns that detect Chinese headings, page markers, and structural cues are preserved unchanged.
3. **Chinese-named templates are aliased, not deleted.** Existing projects referencing Chinese directory names must not break. Aliases are added to index files; originals stay.
4. **Routing triggers are English-first, Chinese-aliased.** `AGENTS.md` and `SKILL.md` trigger phrases gain English primary examples; Chinese examples are retained as secondary aliases so existing Chinese-language users are not broken.
5. **Bilingual UI is preserved; only the default locale changes.** The `zh` locale block in `app.js` stays intact. Only the initial locale selection switches from `zh` to `en`.
6. **No deletions without an alias or archive.** Every removed or renamed item must have a documented fallback path.
7. **Surgical changes only.** Each file edit touches only the lines identified in this plan. Adjacent code, comments, and formatting are left as-is.

---

## 3. Tier-by-Tier Action Plan

### Tier 1 — Generation Output

These files directly produce Chinese text in generated decks, charts, or UI. Highest priority.

---

#### 1.1 `skills/ppt-master/scripts/confirm_ui/static/app.js` — UI locale default

**File**: [`app.js`](../skills/ppt-master/scripts/confirm_ui/static/app.js)

**Finding**: The `MESSAGES` object contains both `en` and `zh` locale blocks. The initial locale is set to `zh` (or auto-detected from browser, defaulting to Chinese). The `en` block is complete and correct.

**Action**: Change the default locale selection from `zh` to `en`. Locate the line that sets the initial locale (e.g. `var lang = navigator.language.startsWith('zh') ? 'zh' : 'en'` or a hardcoded `'zh'`) and invert the logic so `en` is the default unless the browser explicitly reports a `zh` locale.

**Risk**: Low. The `zh` locale block is untouched; Chinese-locale browsers still receive Chinese UI. Only the fallback changes.

---

#### 1.2 `skills/ppt-master/scripts/confirm_ui/static/catalogs.json` — catalog label defaults

**File**: [`catalogs.json`](../skills/ppt-master/scripts/confirm_ui/static/catalogs.json)

**Finding**: Each catalog entry has three label fields: `label` (used as the display fallback), `label_zh`, and `label_en`. Currently `label` is set to the Chinese string (e.g. `"label": "公众号头图 2.35:1"`). When the UI renders in English mode it uses `label_en`; when it falls back to `label` it gets Chinese.

**Action**: For every entry where `label` differs from `label_en`, set `label` equal to `label_en`. The `label_zh` field is preserved unchanged. Affected entries include canvas formats (`wechat`, `xiaohongshu`, `moments`, `story`, `banner`), icon options (`不用图标`, `AI 生成`, `Web 来源`, `用户提供`, `占位符`, `不使用图片`), and delivery purpose labels.

**Risk**: Low. `label_zh` is untouched; the change only affects the unlocalized fallback string.

---

#### 1.3 `skills/ppt-master/scripts/tts_backends/backend_edge.py` — voice descriptions

**File**: [`backend_edge.py`](../skills/ppt-master/scripts/tts_backends/backend_edge.py)

**Finding**: `COMMON_VOICES` is a list of tuples `(locale, voice_id, description)`. All description strings are in Chinese (e.g. `"女声，普通话，清晰自然，默认推荐"`).

**Action**: Translate the third element of each tuple to English. Example translations:

| Current (Chinese)                    | Replacement (English)                                         |
| ------------------------------------ | ------------------------------------------------------------- |
| `"女声，普通话，清晰自然，默认推荐"` | `"Female, Mandarin, clear and natural — default recommended"` |
| `"女声，普通话，明亮"`               | `"Female, Mandarin, bright"`                                  |
| `"男声，普通话，稳重"`               | `"Male, Mandarin, composed"`                                  |
| `"男声，普通话，年轻"`               | `"Male, Mandarin, youthful"`                                  |
| `"男声，普通话，少年感"`             | `"Male, Mandarin, boyish"`                                    |
| `"男声，普通话，播报感"`             | `"Male, Mandarin, broadcast style"`                           |
| `"女声，粤语"`                       | `"Female, Cantonese"`                                         |
| `"男声，粤语"`                       | `"Male, Cantonese"`                                           |
| `"女声，台湾普通话"`                 | `"Female, Taiwanese Mandarin"`                                |
| `"男声，台湾普通话"`                 | `"Male, Taiwanese Mandarin"`                                  |
| `"女声，美式英语"`                   | `"Female, American English"`                                  |
| `"男声，美式英语"`                   | `"Male, American English"`                                    |
| `"女声，英式英语"`                   | `"Female, British English"`                                   |
| `"男声，英式英语"`                   | `"Male, British English"`                                     |

**Risk**: None. These strings are display-only; they do not affect voice selection logic.

---

#### 1.4 Chart SVG templates — default series names

**File location**: [`skills/ppt-master/templates/charts/`](../skills/ppt-master/templates/charts/)

**Finding**: Chart SVG templates contain default series name placeholders. The audit identified `系列1` and `系列2` as default series labels in chart templates. (Search confirmed 0 hits in the `templates/` tree at time of audit — verify the exact files during implementation by running `grep -r "系列" skills/ppt-master/templates/charts/`.)

**Action**: Replace every occurrence of `系列1` with `Series 1` and `系列2` with `Series 2` across all chart template files. If additional Chinese placeholder strings are found during the grep, translate them to English equivalents.

**Risk**: Low. These are placeholder strings in template files, not runtime logic.

---

#### 1.5 `skills/ppt-master/references/executor-base.md` — filename and notes examples

**File**: [`executor-base.md`](../skills/ppt-master/references/executor-base.md)

**Finding**: Line 225 contains the example `01_封面.svg / 02_目录.svg / 03_核心优势.svg` as the primary filename example, with the English example listed second. Lines 393–399 contain a full Chinese deck notes example block. Line 409 contains `其他语言` in a note.

**Action**:

1. On line 225, swap the order so the English example (`01_cover.svg / 02_agenda.svg / 03_key_benefits.svg`) appears first and the Chinese example is demoted to a secondary parenthetical.
2. On lines 393–399, move the Chinese deck example block below the English deck example block (lines 401–407), so English is the primary illustration.
3. On line 409, translate `其他语言` to `Other languages` in the note.

**Risk**: Low. These are illustrative examples in an AI instruction file; reordering does not change the rule, only the default mental model the AI builds from the examples.

---

#### 1.6 `skills/ppt-master/workflows/generate-audio.md` — voice template default

**File**: [`generate-audio.md`](../skills/ppt-master/workflows/generate-audio.md)

**Finding**: Line 7 lists `"生成音频" / "录制旁白"` as the primary trigger phrases before the English equivalents. Step 1 (line 36) gives special handling for Chinese locale selection as the primary example. The workflow implicitly treats Chinese as the default deck language.

**Action**:

1. On line 7, reorder trigger phrases so English examples (`"narrated PPT"`, `"video export with voice"`) appear first, with Chinese aliases (`"生成音频"`, `"录制旁白"`) retained in parentheses after.
2. In Step 1, rewrite the locale-detection note so English (`en-US`) is the primary example and Chinese locale selection is described as a special case, not the default.

**Risk**: Low. The workflow logic is unchanged; only the ordering of examples shifts.

---

### Tier 2 — Routing Triggers

These files contain Chinese phrases that must be typed to activate workflows. English equivalents must become the primary trigger, with Chinese retained as aliases.

---

#### 2.1 `AGENTS.md` — workflow routing triggers

**File**: [`AGENTS.md`](../AGENTS.md)

**Finding**: Six Chinese trigger phrases gate workflow routing:

| Chinese trigger                             | Workflow       |
| ------------------------------------------- | -------------- |
| `"把这份 PPT 美化一下"`                     | beautify-pptx  |
| `"重新排版，内容别动"`                      | beautify-pptx  |
| `"继续生成 projects/<x>"`                   | resume-execute |
| `"建立品牌"`                                | create-brand   |
| `"跑一下视觉自检 / 视觉回看 / 视觉 rubric"` | visual-review  |

**Action**: For each routing block, add an English primary trigger phrase before the Chinese alias. Pattern: `"<English phrase>" / "<Chinese alias>"`. Specific additions:

| Chinese trigger                             | English primary to add                           |
| ------------------------------------------- | ------------------------------------------------ |
| `"把这份 PPT 美化一下"`                     | `"beautify this deck"` / `"re-layout this deck"` |
| `"重新排版，内容别动"`                      | `"re-layout, keep content"`                      |
| `"继续生成 projects/<x>"`                   | `"continue generating projects/<x>"`             |
| `"建立品牌"`                                | `"set up brand"`                                 |
| `"跑一下视觉自检 / 视觉回看 / 视觉 rubric"` | `"visual review"` / `"visual self-check"`        |

**Risk**: Low. Chinese aliases are preserved; existing Chinese-language users are unaffected.

---

#### 2.2 `skills/ppt-master/SKILL.md` — skill activation triggers

**File**: [`SKILL.md`](../skills/ppt-master/SKILL.md)

**Finding**: Chinese trigger phrases `"生成PPT"`, `"做PPT"`, `"制作演示文稿"` activate the main pipeline. Split-mode resumption phrases are also Chinese-primary.

**Action**: Add English primary triggers (`"generate a presentation"`, `"make a PPT"`, `"create slides"`) before the Chinese aliases in the trigger section. Retain all Chinese phrases as secondary aliases.

**Risk**: Low. Additive change only.

---

#### 2.3 Workflow files with Chinese trigger examples

**Files**:

- [`beautify-pptx.md`](../skills/ppt-master/workflows/beautify-pptx.md)
- [`resume-execute.md`](../skills/ppt-master/workflows/resume-execute.md)
- [`create-brand.md`](../skills/ppt-master/workflows/create-brand.md)
- [`topic-research.md`](../skills/ppt-master/workflows/topic-research.md)
- [`template-fill-pptx.md`](../skills/ppt-master/workflows/template-fill-pptx.md)

**Finding**: Each workflow's "When to Run" or trigger section lists Chinese example phrases as the primary activation signal.

**Action**: In each file's trigger section, prepend English example phrases before the Chinese aliases. Do not remove Chinese examples. Follow the same pattern as §2.1.

**Risk**: Low. Additive change; no logic altered.

---

### Tier 3 — Template Assets

Chinese-named directories contain brand and deck templates. Renaming breaks any project that references the directory by name. The safe approach is aliasing via index files.

---

#### 3.1 Deck template directories

**Location**: [`skills/ppt-master/templates/decks/`](../skills/ppt-master/templates/decks/)

**Chinese-named directories**:

| Chinese name | Suggested English alias                                   |
| ------------ | --------------------------------------------------------- |
| `中国电建/`  | `powerchina/`                                             |
| `重庆大学/`  | `chongqing-university/`                                   |
| `招商银行/`  | `cmb/` (China Merchants Bank)                             |
| `中汽研/`    | `catarc/` (China Automotive Technology & Research Center) |
| `中国电信/`  | `china-telecom/`                                          |

**Action**:

1. Open [`skills/ppt-master/templates/decks/decks_index.json`](../skills/ppt-master/templates/decks/decks_index.json).
2. For each Chinese-named entry, add an `"alias"` field mapping the English slug to the Chinese directory path. Example: `"alias": "powerchina"` alongside `"path": "中国电建/"`.
3. Update any script or reference that resolves template paths by name to check the alias field before failing.
4. Do NOT rename or move the directories themselves in this migration. Directory renaming is a follow-up task after alias resolution is confirmed working.
5. Inside each `design_spec.md` within these directories: translate the `deck_id` value to the English alias slug. Leave font stacks (`"微软雅黑"` etc.) and asset filenames unchanged — those are brand-specific assets, not generation defaults.

**Risk**: Medium. Any hardcoded path reference to the Chinese directory name in scripts or user projects will still work because the directories are not moved. The alias only adds a new resolution path.

---

#### 3.2 Brand template directories

**Location**: [`skills/ppt-master/templates/brands/`](../skills/ppt-master/templates/brands/)

**Chinese-named directories**:

| Chinese name | Suggested English alias |
| ------------ | ----------------------- |
| `中国电建/`  | `powerchina/`           |
| `中汽研/`    | `catarc/`               |

**Action**: Same alias approach as §3.1. Add alias entries to [`skills/ppt-master/templates/brands/brands_index.json`](../skills/ppt-master/templates/brands/brands_index.json). Do not move directories.

**Risk**: Low. Existing brand references by Chinese name continue to resolve.

---

### Tier 4 — Documentation

User-facing documentation. Chinese docs are a feature for Chinese-speaking users; they should be archived in place, not deleted. Bilingual nav links are preserved.

---

#### 4.1 `docs/zh/` — Chinese documentation files

**Files**: 10 files under [`docs/zh/`](../docs/zh/)

**Action**: No changes required in this migration. The `docs/zh/` directory is a deliberate localization artifact. It is not a generation-output concern. Add a note to the directory's `README` (or create one if absent) stating that these files are the Chinese-language mirror of the English docs and are maintained separately.

**Risk**: None.

---

#### 4.2 `README_CN.md` — Chinese README

**File**: [`README_CN.md`](../README_CN.md)

**Action**: No changes required. This file is a user-facing localization artifact, not a generation-output concern.

**Risk**: None.

---

#### 4.3 Bilingual nav headers in root-level docs

**Files**: 8 root-level docs with `[中文]` nav links.

**Action**: No changes required. Bilingual navigation is a feature. The links point to `docs/zh/` which is preserved.

**Risk**: None.

---

### Tier 5 — Internal Parsers

**These files must NOT be changed.** See §4 (What NOT to Change) for the full rationale.

**Files**:

- [`skills/ppt-master/scripts/source_to_md/pdf_to_md.py`](../skills/ppt-master/scripts/source_to_md/pdf_to_md.py)
- [`skills/ppt-master/scripts/source_to_md/web_to_md.py`](../skills/ppt-master/scripts/source_to_md/web_to_md.py)
- [`skills/ppt-master/scripts/total_md_split.py`](../skills/ppt-master/scripts/total_md_split.py)
- [`skills/ppt-master/scripts/project_utils.py`](../skills/ppt-master/scripts/project_utils.py)

**Finding**: These files contain Chinese regex patterns (e.g. `第\s*(\d{1,3})\s*[页张]` in `total_md_split.py`) and legacy Chinese spec filenames (`设计规范与内容大纲.md`, `设计规范.md` in `project_utils.py`). These patterns are input parsers, not output generators.

**Action**: No changes. Document in the validation checklist that these patterns are intentionally preserved.

---

## 4. What NOT to Change

| Item                                                                           | Reason                                                                                                                                    |
| ------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Chinese regex in `pdf_to_md.py`, `web_to_md.py`                                | Required to parse Chinese-language source PDFs and web pages. Removing breaks Chinese source ingestion.                                   |
| `第X页` pattern in `total_md_split.py`                                         | Required to split notes files generated from Chinese-language decks.                                                                      |
| Legacy spec filenames in `project_utils.py` (`设计规范与内容大纲.md` etc.)     | Required to validate and list existing projects that used the old Chinese naming convention.                                              |
| `朋友圈` / `小红书` canvas aliases in `project_utils.py`                       | These are input aliases for canvas format selection; they map to English internal IDs. Removing breaks users who type these canvas names. |
| `zh` locale block in `app.js`                                                  | The bilingual toggle is a feature. Only the _default_ locale changes, not the availability of Chinese.                                    |
| `docs/zh/` and `README_CN.md`                                                  | User-facing localization artifacts. Not generation-output concerns.                                                                       |
| Chinese-named template directories (physical paths)                            | Existing projects reference these by path. Physical rename is a follow-up task after alias resolution is confirmed.                       |
| Font stacks like `"微软雅黑"` inside brand `design_spec.md` files              | These are brand-specific assets that belong to Chinese corporate brands. They are not generation defaults.                                |
| Chinese example notes in `executor-base.md` (the content of the example block) | The Chinese example block is kept; only its position relative to the English example changes (English moves first).                       |

---

## 5. Migration Sequence

Execute tiers in this order to minimize risk and allow incremental validation at each step.

```
Step 1 → Tier 1.3  backend_edge.py voice descriptions       (zero-risk, display-only)
Step 2 → Tier 1.4  Chart SVG series names                   (zero-risk, template strings)
Step 3 → Tier 1.2  catalogs.json label defaults             (low-risk, UI fallback strings)
Step 4 → Tier 1.1  app.js locale default                    (low-risk, test UI after)
Step 5 → Tier 1.5  executor-base.md example reordering      (low-risk, AI instruction file)
Step 6 → Tier 1.6  generate-audio.md trigger reordering     (low-risk, workflow file)
Step 7 → Tier 2.3  Workflow file trigger phrases             (low-risk, additive)
Step 8 → Tier 2.1  AGENTS.md routing triggers               (medium — test routing after)
Step 9 → Tier 2.2  SKILL.md skill triggers                  (medium — test routing after)
Step 10 → Tier 3.1 decks_index.json aliases                 (medium — test template resolution)
Step 11 → Tier 3.2 brands_index.json aliases                (medium — test brand resolution)
Step 12 → Tier 4   Documentation (no-op — document only)
```

**Rationale for this order**:

- Steps 1–4 are pure string changes with no routing or resolution logic. They can be done and validated independently.
- Steps 5–7 are AI instruction and workflow files. Changes are additive (reordering, prepending). Safe to batch.
- Steps 8–9 touch routing logic. Do these after the lower-risk changes are confirmed, and test end-to-end routing after each.
- Steps 10–11 require index file edits and potentially script changes. Do last so the alias resolution code can be tested against the already-confirmed English defaults.

---

## 6. Validation Checklist

After completing all steps, verify the following:

### Generation output

- [ ] Open the Confirm UI (`python3 skills/ppt-master/scripts/confirm_ui/server.py <project_path> --daemon --wait`) — the page loads in English by default without any browser locale override.
- [ ] All catalog dropdown labels display in English (canvas formats, icon options, image usage options, delivery purpose).
- [ ] Run `python3 skills/ppt-master/scripts/notes_to_audio.py --list-voices --locale en-US` — voice descriptions print in English.
- [ ] Inspect chart SVG templates: `grep -r "系列" skills/ppt-master/templates/charts/` returns zero results.
- [ ] Generate a test deck with no language specified — the output SVGs contain English text, not Chinese.

### Routing triggers

- [ ] Send `"generate a presentation about X"` to the agent — the main SKILL.md pipeline activates.
- [ ] Send `"beautify this deck"` — the `beautify-pptx` workflow activates.
- [ ] Send `"continue generating projects/test"` — the `resume-execute` workflow activates.
- [ ] Send `"set up brand"` — the `create-brand` workflow activates.
- [ ] Send `"visual review"` — the `visual-review` workflow activates.
- [ ] Verify Chinese aliases still work: send `"把这份 PPT 美化一下"` — `beautify-pptx` still activates.

### Template resolution

- [ ] Reference a deck template by English alias (e.g. `powerchina`) — the template resolves to `中国电建/`.
- [ ] Reference a deck template by original Chinese name — still resolves (no regression).
- [ ] Reference a brand by English alias (e.g. `catarc`) — resolves to `中汽研/`.

### Source ingestion (Tier 5 — must NOT regress)

- [ ] Run `python3 skills/ppt-master/scripts/source_to_md/pdf_to_md.py <chinese_pdf>` — Chinese PDF converts correctly.
- [ ] Run `python3 skills/ppt-master/scripts/total_md_split.py <project_with_chinese_notes>` — Chinese `第X页` headings split correctly.
- [ ] Run `python3 skills/ppt-master/scripts/project_manager.py validate <old_chinese_spec_project>` — legacy Chinese spec filenames are still detected.

### Documentation

- [ ] `docs/zh/` files are intact and accessible.
- [ ] `README_CN.md` is intact.
- [ ] Bilingual nav links in root docs resolve correctly.

---

## 7. Rollback Notes

### Git branch strategy

Before starting any changes, create a dedicated branch:

```bash
git checkout -b feat/english-migration
```

Each tier can be committed separately with a descriptive message, e.g.:

```
git commit -m "tier1: translate backend_edge.py voice descriptions to English"
git commit -m "tier1: set catalogs.json label defaults to English"
git commit -m "tier1: switch app.js default locale to en"
```

This allows cherry-pick revert of any individual tier without rolling back the entire migration.

### Per-tier rollback

| Tier                         | Rollback method                                                                                             |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Tier 1.1 (app.js locale)     | `git revert <commit>` or manually restore the locale detection line                                         |
| Tier 1.2 (catalogs.json)     | `git revert <commit>` — `label_zh` fields are untouched so Chinese UI is unaffected                         |
| Tier 1.3 (backend_edge.py)   | `git revert <commit>` — display-only change                                                                 |
| Tier 1.4 (chart SVGs)        | `git revert <commit>`                                                                                       |
| Tier 1.5 (executor-base.md)  | `git revert <commit>` — example reordering only                                                             |
| Tier 1.6 (generate-audio.md) | `git revert <commit>`                                                                                       |
| Tier 2 (routing triggers)    | `git revert <commit>` — Chinese aliases were never removed, so revert restores English-only trigger removal |
| Tier 3 (index aliases)       | Remove the added alias fields from `decks_index.json` / `brands_index.json`; no directories were moved      |

### What cannot be easily rolled back

- If physical directory renames are performed (explicitly out of scope for this migration), rollback requires renaming back AND updating any project files that referenced the new English path. This is why physical renames are deferred to a follow-up task.

### Verification after rollback

After any revert, re-run the Tier 5 source ingestion checks from §6 to confirm Chinese source document processing is still intact.

---

_Document version: 1.0 — created as planning artifact; no code changes made._
