---
brand_id: rakuten_mobile
kind: brand
summary: Rakuten Mobile brand — 5G network decks, telecom product launches, subscriber growth reports, MNO/MVNO partner presentations
primary_color: "#FF008C"
---

# Rakuten Mobile Brand Specification

> Identity-only preset. No SVG page roster — pages are composed freely under these constraints.

## I. Brand Overview

| Property   | Value                                                                                                                            |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Brand Name | Rakuten Mobile                                                                                                                   |
| Use Cases  | Network coverage presentations, 5G technology decks, telecom product launches, subscriber growth reports, MNO/MVNO partner decks |
| Tone       | Tech-forward, modern, confident, network-centric                                                                                 |

## II. Color Scheme

| Role       | HEX       | Provenance | Notes                                                                                                                         |
| ---------- | --------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------- |
| primary    | `#FF008C` | fact       | Rakuten Mobile magenta — used in the Rakuten Mobile wordmark and primary sub-brand identity |
| secondary  | `#000000` | fact       | Black — structural elements, text on light backgrounds, high-contrast pairings with pink                                      |
| bg         | `#FFFFFF` | fact       | Pure white page background                                                                                                    |
| accent     | `#777B7E` | fact       | Gray — footer confidential label, secondary UI elements                                                                       |
| text       | `#1A1A1A` | approx     | Near-black — body text, captions, chart labels                                                                                |
| surface    | `#F5F5F5` | approx     | Light gray — card backgrounds, section dividers                                                                               |
| muted-text | `#666666` | approx     | Medium gray — secondary text, footnotes, metadata                                                                             |

The first two rows (pink + black) form the core Rakuten Mobile palette; white background is the canonical canvas. The gray accent is used in footer labels and secondary UI elements. The text / surface / muted-text rows are presentation conventions derived to match Rakuten Mobile's clean, tech-forward UI. Strategist may rotate pink and black into dominant roles per page rhythm.

## III. Typography

| Role  | Family                                                | Weight |
| ----- | ----------------------------------------------------- | ------ |
| title | `"Rakuten Sans", "Helvetica Neue", Arial, sans-serif` | 700    |
| body  | `"Rakuten Sans", "Helvetica Neue", Arial, sans-serif` | 400    |

> `Rakuten Sans` is a proprietary brand typeface shared across the Rakuten Group and is unlikely to be installed on viewer machines. Decks should either embed the font into the PPTX or accept the `Helvetica Neue` → `Arial` fallback chain. When locking, Strategist notes "official Rakuten Sans typeface requires install or PPTX embed".

## IV. Logo

No logo assets are bundled in this preset. Add logo files to this directory and update this section when official Rakuten Mobile brand assets are available.

## V. Voice & Tone

- Formality: professional-neutral
- Person: we / you
- Emoji: avoid
- Abbreviations: spell-out-first-use

## VI. Icon Style

- Preference: stroke

> Stroke icons align with Rakuten Mobile's clean, tech-forward network UI aesthetic — consistent with the precision and clarity expected in telecom and infrastructure contexts. When the deck uses `templates/icons/`, prefer `tabler` or `lucide` stroke families; avoid filled libraries to stay consistent with Rakuten Mobile's product UI visual language.

## VII. Layout Rules

- **Footer bar**: Required on every slide. Place at the bottom of the canvas.
  - Bottom-left — Rakuten Mobile wordmark: `<text>` element at `x="40"`, `y="[canvas_height - 28]"`, `font-size="14"`, `font-weight="bold"`, `fill="#FF008C"`, `font-family="sans-serif"`, content `Rakuten Mobile`
  - Bottom-right — Confidential label: `<text>` element at `x="[canvas_width - 40]"`, `y="[canvas_height - 28]"`, `font-size="10"`, `fill="#777B7E"`, `text-anchor="end"`, `font-family="sans-serif"`, content `Confidential`
  - Replace `[canvas_height]` and `[canvas_width]` with the actual SVG canvas dimensions for each slide.
- **Background**: MUST be white (`#FFFFFF`) on all slides. Do not use dark-background visual styles (dark-tech, blueprint, dark-cinematic, chalkboard, ink-wash, pixel-art, or any style with a dark/black canvas).
