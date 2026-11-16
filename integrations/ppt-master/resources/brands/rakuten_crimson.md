---
brand_id: rakuten_crimson
kind: brand
summary: Rakuten Group corporate brand — investor relations, group company reports, brand partnership decks, sports sponsorship
primary_color: "#BF0000"
---

# Rakuten Crimson Brand Specification

> Identity-only preset. No SVG page roster — pages are composed freely under these constraints.

## I. Brand Overview

| Property   | Value                                                                                                                     |
| ---------- | ------------------------------------------------------------------------------------------------------------------------- |
| Brand Name | Rakuten Group                                                                                                             |
| Use Cases  | Corporate presentations, investor relations, group company reports, brand partnership decks, sports sponsorship materials |
| Tone       | Bold, energetic, optimistic, empowering ("Empowering people")                                                             |

## II. Color Scheme

| Role       | HEX       | Provenance | Notes                                                                                         |
| ---------- | --------- | ---------- | --------------------------------------------------------------------------------------------- |
| primary    | `#BF0000` | fact       | Rakuten Crimson — official brand red, used in the Rakuten wordmark and primary brand identity |
| secondary  | `#000000` | fact       | Black — structural elements, text on light backgrounds, high-contrast pairings with crimson   |
| bg         | `#FFFFFF` | fact       | Pure white page background                                                                    |
| accent     | `#777B7E` | fact       | Gray — footer confidential label, secondary UI elements                                       |
| text       | `#1A1A1A` | approx     | Near-black — body text, captions, chart labels                                                |
| surface    | `#F5F5F5` | approx     | Light gray — card backgrounds, section dividers                                               |
| muted-text | `#666666` | approx     | Medium gray — secondary text, footnotes, metadata                                             |

The first two rows (crimson + black) are the official brand pair; white background is the canonical Rakuten canvas. The gray accent is used in footer labels and secondary UI elements. The text / surface / muted-text rows are presentation conventions derived to match Rakuten's bold, high-contrast product aesthetic. Strategist may rotate crimson and black into dominant roles per page rhythm.

## III. Typography

| Role  | Family                                                | Weight |
| ----- | ----------------------------------------------------- | ------ |
| title | `"Rakuten Sans", "Helvetica Neue", Arial, sans-serif` | 700    |
| body  | `"Rakuten Sans", "Helvetica Neue", Arial, sans-serif` | 400    |

> `Rakuten Sans` is a proprietary brand typeface and is unlikely to be installed on viewer machines. Decks should either embed the font into the PPTX or accept the `Helvetica Neue` → `Arial` fallback chain. When locking, Strategist notes "official Rakuten Sans typeface requires install or PPTX embed".

## IV. Logo

Rakuten uses a dual-asset brand system — pick by context, never combine on the same page.

| File                       | Form                                           | Usage                                                                                                                         |
| -------------------------- | ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `./rakuten_logo.svg`       | "Rakuten" wordmark in crimson (primary lockup) | Cover hero, ending sign-off, any moment the full brand reads at a glance                                                      |
| `./rakuten_eagle_mark.svg` | Eagle symbol mark (compact)                    | Header / footer corners, page-number neighbors, tight badges, any small-size moment where the wordmark would become illegible |

- Cover: prefer the wordmark
- Per-page: optional — only when the wordmark or eagle mark genuinely fits the layout; do not stamp every page
- Clearspace: leave at least 0.5× logo height of empty space on all sides; never overlap text or photographic backgrounds
- Mark color is fixed at `#BF0000`; wordmark inherits crimson on white backgrounds; use white reversed-out version on dark or crimson backgrounds

## V. Voice & Tone

- Formality: professional-neutral
- Person: we / you
- Emoji: avoid
- Abbreviations: spell-out-first-use

## VI. Icon Style

- Preference: filled

> Filled icons align with Rakuten's bold, high-contrast product UI aesthetic across Rakuten Ichiba, Rakuten Pay, and other group services. When the deck uses `templates/icons/`, prefer `tabler-filled` or `chunk-filled`; avoid stroke-only libraries to stay consistent with Rakuten's visual language.

## VII. Layout Rules

- **Footer bar**: Required on every slide. Place at the bottom of the canvas.
  - Bottom-left — Rakuten wordmark: `<text>` element at `x="40"`, `y="[canvas_height - 28]"`, `font-size="14"`, `font-weight="bold"`, `fill="#BF0000"`, `font-family="sans-serif"`, content `Rakuten`
  - Bottom-right — Confidential label: `<text>` element at `x="[canvas_width - 40]"`, `y="[canvas_height - 28]"`, `font-size="10"`, `fill="#777B7E"`, `text-anchor="end"`, `font-family="sans-serif"`, content `Confidential`
  - Replace `[canvas_height]` and `[canvas_width]` with the actual SVG canvas dimensions for each slide.
- **Background**: MUST be white (`#FFFFFF`) on all slides. Do not use dark-background visual styles (dark-tech, blueprint, dark-cinematic, chalkboard, ink-wash, pixel-art, or any style with a dark/black canvas).
