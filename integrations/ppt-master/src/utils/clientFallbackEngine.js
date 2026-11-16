// Client-side resilience layer: default config, a deterministic Strategist and a deterministic SVG renderer.
// Used whenever the server endpoints fail so the UI never ends up blank or blocked.

const SANS = 'Helvetica Neue, Arial, sans-serif';
const RAKUTEN = 'Rakuten Sans, Helvetica Neue, Arial, sans-serif';
const mk = (id, name, description, colors, fonts, tone) => ({ id, name, description, colors, fonts, tone });

export const FALLBACK_BRANDS = [
  mk('rakuten-mobile', 'Rakuten Mobile', 'Tech-forward telecom brand: magenta on white with black structure.',
    { primary: '#FF008C', secondary: '#000000', accent: '#777B7E', background: '#FFFFFF', surface: '#F5F5F5', text: '#1A1A1A', muted: '#666666' },
    { title_font: RAKUTEN, body_font: RAKUTEN }, 'Tech-forward, modern, confident, network-centric.'),
  mk('rakuten-crimson', 'Rakuten Crimson', 'Rakuten Group corporate identity: bold crimson, black and white.',
    { primary: '#BF0000', secondary: '#000000', accent: '#777B7E', background: '#FFFFFF', surface: '#F5F5F5', text: '#1A1A1A', muted: '#666666' },
    { title_font: RAKUTEN, body_font: RAKUTEN }, 'Bold, energetic, corporate; Empowering people.'),
  mk('anthropic', 'Anthropic', 'Warm terracotta on soft cream, editorial and calm.',
    { primary: '#D97757', secondary: '#141413', accent: '#6A9BCC', background: '#FAF9F5', surface: '#F0EFEA', text: '#141413', muted: '#6B6A64' },
    { title_font: 'Georgia, Times New Roman, serif', body_font: SANS }, 'Thoughtful, human, editorial.'),
  mk('google', 'Google', 'Four-colour Google palette on clean white.',
    { primary: '#4285F4', secondary: '#34A853', accent: '#FBBC05', background: '#FFFFFF', surface: '#F8F9FA', text: '#202124', muted: '#5F6368' },
    { title_font: 'Arial, Helvetica, sans-serif', body_font: 'Arial, Helvetica, sans-serif' }, 'Friendly, clear, helpful.'),
  mk('corporate-blue', 'Corporate Blue', 'Navy and azure corporate look for formal reports.',
    { primary: '#00418D', secondary: '#0070C0', accent: '#F29F05', background: '#FFFFFF', surface: '#F4F6F9', text: '#1F2937', muted: '#6B7280' },
    { title_font: 'Arial, Helvetica, sans-serif', body_font: 'Arial, Helvetica, sans-serif' }, 'Formal, trustworthy, precise.'),
  mk('dark-tech', 'Dark Tech', 'Cyan and indigo on dark slate for technical decks.',
    { primary: '#38BDF8', secondary: '#818CF8', accent: '#34D399', background: '#0F172A', surface: '#1E293B', text: '#F8FAFC', muted: '#94A3B8' },
    { title_font: SANS, body_font: SANS }, 'Technical, modern, precise.'),
  mk('none', 'Custom / Auto', 'No brand: the AI Strategist selects a harmonious palette.',
    { primary: '#2563EB', secondary: '#0F172A', accent: '#F59E0B', background: '#FFFFFF', surface: '#F1F5F9', text: '#0F172A', muted: '#64748B' },
    { title_font: SANS, body_font: SANS }, 'Neutral and professional.'),
];

export const FALLBACK_CANVASES = [
  { id: 'ppt169', label: '16:9 Widescreen (1280x720)', width: 1280, height: 720 },
  { id: 'ppt43', label: '4:3 Standard (1024x768)', width: 1024, height: 768 },
  { id: 'xiaohongshu', label: '3:4 Portrait (1242x1660)', width: 1242, height: 1660 },
];

export const FALLBACK_CONFIG = {
  models: [],
  defaultModel: '',
  brands: FALLBACK_BRANDS,
  canvasFormats: FALLBACK_CANVASES,
  settings: { default_brand: 'rakuten-mobile' },
  remoteBackend: { configured: false, url: '', reachable: false },
  isFallback: true,
};

/** Merge a (possibly partial / invalid) server config with the defaults. */
export function normalizeConfig(c) {
  const cfg = c && typeof c === 'object' ? c : {};
  return {
    ...FALLBACK_CONFIG,
    ...cfg,
    models: Array.isArray(cfg.models) ? cfg.models : [],
    brands: Array.isArray(cfg.brands) && cfg.brands.length ? cfg.brands : FALLBACK_BRANDS,
    canvasFormats: Array.isArray(cfg.canvasFormats) && cfg.canvasFormats.length ? cfg.canvasFormats : FALLBACK_CANVASES,
    settings: cfg.settings && typeof cfg.settings === 'object' && !Array.isArray(cfg.settings) ? cfg.settings : FALLBACK_CONFIG.settings,
    remoteBackend: cfg.remoteBackend && typeof cfg.remoteBackend === 'object' ? cfg.remoteBackend : FALLBACK_CONFIG.remoteBackend,
    isFallback: !(Array.isArray(cfg.brands) && cfg.brands.length),
  };
}

/** Normalise any project-like object so `.slides`, `.svgs`, etc. can always be dereferenced. */
export function normalizeProject(p) {
  if (!p || typeof p !== 'object' || Array.isArray(p)) return null;
  return {
    ...p,
    slides: Array.isArray(p.slides) ? p.slides : [],
    svgs: p.svgs && typeof p.svgs === 'object' && !Array.isArray(p.svgs) ? p.svgs : {},
    eight_confirmations: p.eight_confirmations && typeof p.eight_confirmations === 'object' ? p.eight_confirmations : {},
    design_spec: p.design_spec && typeof p.design_spec === 'object' ? p.design_spec : { colors: {}, fonts: {} },
  };
}

// ---------------------------------------------------------------------------------------------- strategist

function newId() {
  return `p${new Date().toISOString().replace(/\D/g, '').slice(2, 14)}${Math.random().toString(16).slice(2, 8)}`;
}

function clip(t, n) {
  const s = String(t || '').trim();
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function sourcePoints(source) {
  const pts = [];
  for (const raw of String(source || '').split(/\r?\n/)) {
    const line = raw.replace(/^[#>\-*\d.)\s]+/, '').trim();
    if (line.length >= 12 && line.length <= 140) pts.push(line);
    if (pts.length >= 40) break;
  }
  return pts;
}

const THEMES = [
  ['Why this matters now', 'Context and urgency', ['The landscape is shifting faster than planning cycles', 'Stakeholders expect measurable progress', 'Delay increases cost and risk']],
  ['The core insight', 'What the evidence says', ['One root cause explains most of the gap', 'Data points to a repeatable pattern', 'Addressing it unlocks compounding gains']],
  ['Three pillars carry the strategy', 'How we deliver', ['Foundation: align people, data and tooling', 'Execution: ship in short, measurable cycles', 'Scale: standardise what works']],
  ['Impact shows up in the numbers', 'Measured outcomes', []],
  ['A phased roadmap makes delivery predictable', 'From pilot to scale', ['Discover and baseline', 'Pilot with one team', 'Expand to adjacent teams', 'Operate and optimise']],
  ['Options compared: build, buy or partner', 'Trade-offs at a glance', ['Build: full control, slower start', 'Build: higher long-term ownership cost', 'Buy: fast start, limited flexibility', 'Buy: predictable licence cost']],
  ['Risks are known and manageable', 'Mitigation plan', ['Adoption risk: mitigate with champions and training', 'Data risk: governance from day one', 'Delivery risk: weekly checkpoints', 'Budget risk: stage-gated funding']],
  ['What success looks like in twelve months', 'Targets and owners', []],
  ['Customer and user value comes first', 'Voice of the user', ['Clear problem statement validated with users', 'Prototype feedback shapes every release', 'Satisfaction tracked every sprint']],
  ['Governance keeps everyone aligned', 'Operating rhythm', ['Monthly steering review', 'Weekly delivery stand-up', 'Quarterly strategy refresh']],
];
const LAYOUT_CYCLE = ['two-column', 'three-card', 'two-column', 'kpi-grid', 'process-flow', 'comparison-table', 'four-quadrant', 'kpi-grid', 'two-column', 'three-card'];
const METRIC_SETS = [
  [{ label: 'Baseline efficiency gain', value: '+32%', trend: '+8 pts QoQ' }, { label: 'Cycle time', value: '-41%', trend: '-12 days' }, { label: 'Adoption', value: '87%', trend: '+15 pts' }],
  [{ label: 'Target ROI', value: '3.2x', trend: '+0.6x vs plan' }, { label: 'Payback period', value: '9 mo', trend: '-3 mo' }, { label: 'Satisfaction', value: '4.6/5', trend: '+0.4' }, { label: 'Coverage', value: '92%', trend: '+11 pts' }],
];

/**
 * Client-side Strategist fallback. Returns a complete project object (status 'confirm_pending').
 * @param {object} form  Brief form values.
 * @param {Array}  brands Brand list (server or fallback).
 */
export function synthesizeClientPlan(form, brands = FALLBACK_BRANDS) {
  const f = form || {};
  const list = Array.isArray(brands) && brands.length ? brands : FALLBACK_BRANDS;
  const brand = list.find((b) => b.id === f.brand) || list.find((b) => b.id === 'none') || list[0] || FALLBACK_BRANDS[FALLBACK_BRANDS.length - 1];
  const canvas = FALLBACK_CANVASES.find((c) => c.id === f.canvas_format) || FALLBACK_CANVASES[0];
  const topic = String(f.topic || '').trim() || 'Strategic overview';
  const short = clip(topic, 70);
  const count = Math.max(3, Math.min(30, Number(f.slide_count) || 8));
  const mode = String(f.structure_mode || 'briefing');
  const points = sourcePoints(f.source_material);
  const now = new Date();
  const month = now.toLocaleString('en', { month: 'long', year: 'numeric' });
  const hasBrand = brand.id !== 'none';

  const slides = [];
  slides.push({
    page_type: 'cover', layout_pattern: 'hero', title: short, subtitle: hasBrand ? `${brand.name} · ${month}` : month,
    key_message: 'A clear plan, backed by evidence, ready for decision.', bullets: [], metrics: [],
    visual_notes: 'Full-bleed brand gradient with large title.', speaker_notes: 'Welcome the audience, state the purpose and the decision we need today.',
  });
  let middle = Math.max(1, count - 2);
  const useToc = count >= 8;
  if (useToc) {
    slides.push({
      page_type: 'toc', layout_pattern: 'two-column', title: 'Agenda', subtitle: '', key_message: 'Five questions this briefing answers.',
      bullets: ['Why this matters now', 'The core insight', 'Strategy and pillars', 'Impact and roadmap', 'Risks, success measures and next steps'], metrics: [],
      visual_notes: 'Numbered agenda rows.', speaker_notes: 'Walk through the agenda in 20 seconds.',
    });
    middle -= 1;
  }
  for (let i = 0; i < middle; i += 1) {
    const t = THEMES[i % THEMES.length];
    const layout = LAYOUT_CYCLE[i % LAYOUT_CYCLE.length];
    const bullets = [...t[2]];
    if (bullets.length && points[i]) bullets[0] = points[i];
    const title = i === 1 ? `The core insight behind ${clip(short, 40)}` : t[0];
    slides.push({
      page_type: layout === 'kpi-grid' ? 'data' : layout === 'process-flow' ? 'timeline' : layout === 'comparison-table' ? 'comparison' : 'content',
      layout_pattern: layout, title, subtitle: t[1], key_message: title, bullets,
      metrics: layout === 'kpi-grid' ? METRIC_SETS[i % 2] : [],
      visual_notes: 'Illustrative figures are placeholders; replace with source data.',
      speaker_notes: 'Make the point in one sentence, show the evidence, then bridge to the next slide.',
    });
  }
  const body = slides.slice(0, Math.max(count - 1, 2));
  body.push({
    page_type: 'closing', layout_pattern: 'closing', title: 'Decision and next steps', subtitle: 'Thank you',
    key_message: 'Approve the pilot and name an owner this week.', bullets: ['Approve scope and budget for the pilot', 'Nominate an executive sponsor', 'Kick off within two weeks'], metrics: [],
    visual_notes: 'Brand gradient closing slide.', speaker_notes: 'Restate the conclusion, ask for the decision, open Q&A.',
  });
  const finalSlides = body.map((s, i) => ({ slide_number: i + 1, ...s }));
  const modeLabel = mode.charAt(0).toUpperCase() + mode.slice(1);
  const outline = finalSlides.slice(0, 6).map((s, i) => `${i + 1}. ${s.title}`).join('\n');

  return {
    id: newId(),
    topic,
    source_material: String(f.source_material || ''),
    brand: brand.id,
    canvas_format: canvas.id,
    model: 'client-fallback',
    structure_mode: mode,
    slide_count: finalSlides.length,
    status: 'confirm_pending',
    client_generated: true,
    created_at: now.toISOString(),
    updated_at: now.toISOString(),
    eight_confirmations: {
      goal_and_type: `Persuade and align stakeholders on "${topic}" (decision-oriented ${mode} deck).`,
      audience_and_scenario: 'Business leaders and project owners, reviewed in a meeting and read afterwards.',
      core_conclusion: `Act now on ${short} with a phased, measurable plan.`,
      structure_mode: `${modeLabel} - conclusion first, then supporting arguments and evidence.`,
      page_count: `${finalSlides.length} slides: cover, ${useToc ? 'agenda, ' : ''}content, closing.`,
      style_and_theme: `${brand.name} palette (${brand.colors.primary} primary), clean cards, generous whitespace. ${brand.tone || ''}`.trim(),
      aspect_ratio: canvas.label,
      content_outline_summary: outline,
    },
    design_spec: { colors: { ...brand.colors }, fonts: { ...brand.fonts } },
    slides: finalSlides,
    svgs: {},
  };
}

// ---------------------------------------------------------------------------------------------- svg renderer

const esc = (v) => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&apos;');
const hexOr = (v, fb) => (/^#[0-9a-fA-F]{6}$/.test(String(v || '').trim()) ? String(v).trim().toUpperCase() : fb);
const r = (n, d = 0) => { const m = 10 ** d; return Math.round(n * m) / m; };

function onColor(bg, light = '#FFFFFF', dark = '#111111') {
  const c = (i) => parseInt(bg.slice(i, i + 2), 16) / 255;
  return 0.2126 * c(1) + 0.7152 * c(3) + 0.0722 * c(5) > 0.55 ? dark : light;
}

function wrap(text, maxWidth, fontSize, maxLines = 6) {
  const t = String(text || '').replace(/\s+/g, ' ').trim();
  if (!t) return [];
  const avg = fontSize * 0.54;
  const width = (s) => Array.from(s).reduce((w, ch) => w + (ch.charCodeAt(0) > 255 ? avg * 1.8 : avg), 0);
  const words = t.match(/[^\s\u3000-\u9fff]+\s*|\s+|[\u3000-\u9fff]/g) || [t];
  const lines = [];
  let cur = '';
  for (const word of words) {
    const attempt = cur + word;
    if (cur && width(attempt.trimEnd()) > maxWidth) { lines.push(cur.trimEnd()); cur = word; } else cur = attempt;
  }
  if (cur.trim()) lines.push(cur.trimEnd());
  if (lines.length > maxLines) {
    lines.length = maxLines;
    lines[maxLines - 1] = `${lines[maxLines - 1].replace(/[ .,;:]+$/, '')}…`;
  }
  return lines;
}

function textBlock(lines, x, y, size, fill, font, lh = 1.35, weight = '400', anchor = 'start') {
  if (!lines.length) return '';
  let o = `<text x="${r(x, 1)}" y="${r(y, 1)}" font-family="${esc(font)}" font-size="${r(size, 1)}" font-weight="${weight}" fill="${fill}" text-anchor="${anchor}">`;
  lines.forEach((line, i) => { o += `<tspan x="${r(x, 1)}"${i === 0 ? '' : ` dy="${r(size * lh, 1)}"`}>${esc(line)}</tspan>`; });
  return `${o}</text>`;
}

function kpiCard(x, y, w, h, value, label, trend, k) {
  const { primary, surface, text, muted, titleFont, bodyFont, sc } = k;
  let o = `<rect x="${r(x)}" y="${r(y)}" width="${r(w)}" height="${r(h)}" rx="14" fill="${surface}"/>`;
  o += `<rect x="${r(x)}" y="${r(y + 18 * sc)}" width="${r(6 * sc)}" height="${r(Math.max(h - 36 * sc, 10))}" rx="3" fill="${primary}"/>`;
  let vs = Math.min(72 * sc, h * 0.38);
  while (vs > 24 * sc && String(value).length * vs * 0.58 > w - 40 * sc) vs -= 4 * sc;
  o += `<text x="${r(x + 28 * sc)}" y="${r(y + h * 0.46)}" font-family="${esc(titleFont)}" font-size="${r(vs, 1)}" font-weight="700" fill="${primary}">${esc(value)}</text>`;
  o += textBlock(wrap(label, w - 56 * sc, 18 * sc, 2), x + 28 * sc, y + h * 0.46 + 34 * sc, 18 * sc, text, bodyFont, 1.3);
  if (trend) {
    const up = /^\s*(\+|up|▲|↑)/i.test(trend);
    const down = /^\s*(-|−|down|▼|↓)/i.test(trend);
    o += `<text x="${r(x + 28 * sc)}" y="${r(y + h - 22 * sc)}" font-family="${esc(bodyFont)}" font-size="${r(16 * sc)}" font-weight="700" fill="${up ? '#16A34A' : down ? '#DC2626' : muted}">${up ? '▲' : down ? '▼' : '●'} ${esc(String(trend).slice(0, 28))}</text>`;
  }
  return o;
}

function footer(W, H, M, n, total, fill, font, sc, line = null) {
  let o = '';
  if (line) o += `<line x1="${M}" y1="${r(H - 54 * sc)}" x2="${r(W - M)}" y2="${r(H - 54 * sc)}" stroke="${line}" stroke-opacity="0.3" stroke-width="1.5"/>`;
  return `${o}<text x="${r(W - M)}" y="${r(H - 24 * sc)}" font-family="${esc(font)}" font-size="${r(14 * sc)}" fill="${fill}" text-anchor="end">${n} / ${Math.max(total, n)}</text>`;
}

/**
 * Deterministic SVG renderer (mirrors the PHP SvgFinalizer fallback) so every slide always gets a valid SVG.
 */
export function renderClientDeterministicSvg(slide, designSpec, canvasFormat, totalSlides = 1, topic = '') {
  const s = slide && typeof slide === 'object' ? slide : {};
  const ds = designSpec && typeof designSpec === 'object' ? designSpec : {};
  const canvas = FALLBACK_CANVASES.find((c) => c.id === canvasFormat) || FALLBACK_CANVASES[0];
  const W = canvas.width;
  const H = canvas.height;
  const c = ds.colors || {};
  const f = ds.fonts || {};
  const primary = hexOr(c.primary, '#2563EB');
  const secondary = hexOr(c.secondary, '#0F172A');
  const accent = hexOr(c.accent, '#F59E0B');
  const bg = hexOr(c.background, '#FFFFFF');
  const surface = hexOr(c.surface, '#F5F5F5');
  const text = hexOr(c.text, '#1A1A1A');
  const muted = hexOr(c.muted, '#666666');
  const titleFont = String(f.title_font || SANS).replace(/"/g, '');
  const bodyFont = String(f.body_font || SANS).replace(/"/g, '');
  const n = Number(s.slide_number) || 1;
  const title = String(s.title || topic || 'Untitled');
  const subtitle = String(s.subtitle || '');
  const type = String(s.page_type || 'content');
  const layout = String(s.layout_pattern || 'two-column').toLowerCase();
  const key = String(s.key_message || '');
  const bullets = (Array.isArray(s.bullets) ? s.bullets : []).map(String).filter((b) => b.trim());
  const metrics = (Array.isArray(s.metrics) ? s.metrics : []).filter((m) => m && typeof m === 'object');
  const total = Number(totalSlides) || 1;
  const M = 60;
  const sc = W / 1280;
  const portrait = H > W;
  const k = { primary, surface, text, muted, titleFont, bodyFont, sc };
  let out = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">`;

  if (['cover', 'closing', 'section'].includes(type) || layout === 'hero' || layout === 'closing') {
    const dark = type !== 'section';
    const fg = dark ? onColor(primary) : text;
    out += `<rect x="0" y="0" width="${W}" height="${H}" fill="${dark ? primary : surface}"/>`;
    if (dark) {
      out += `<polygon points="${r(W * 0.62)},0 ${W},0 ${W},${H} ${r(W * 0.42)},${H}" fill="${secondary}" fill-opacity="0.28"/>`;
      out += `<circle cx="${r(W * 0.88)}" cy="${r(H * 0.22)}" r="${r(120 * sc)}" fill="${accent}" fill-opacity="0.35"/>`;
    }
    out += `<rect x="${M}" y="${r(H * 0.3)}" width="${r(90 * sc)}" height="${r(8 * sc)}" rx="4" fill="${dark ? accent : primary}"/>`;
    const ts = (portrait ? 64 : 56) * sc;
    const lines = wrap(title, W - 2 * M, ts * 0.95, 3);
    out += textBlock(lines, M, H * 0.3 + 90 * sc, ts, fg, titleFont, 1.18, '700');
    const yy = H * 0.3 + 90 * sc + lines.length * ts * 1.18 + 20 * sc;
    out += textBlock(wrap(subtitle || key, W - 2 * M - 80 * sc, 26 * sc, 3), M, yy + 20 * sc, 26 * sc, fg, bodyFont, 1.4);
    if (type === 'section') out += `<text x="${W - M}" y="${r(H * 0.45)}" font-family="${esc(titleFont)}" font-size="${r(200 * sc)}" font-weight="700" fill="${primary}" fill-opacity="0.15" text-anchor="end">${String(n).padStart(2, '0')}</text>`;
    if (type === 'closing' && bullets.length) {
      const by = yy + 100 * sc;
      bullets.slice(0, 3).forEach((b, i) => {
        out += `<circle cx="${M + 8}" cy="${r(by + i * 40 * sc - 7)}" r="6" fill="${accent}"/>`;
        out += textBlock(wrap(b, W - 2 * M - 40, 22 * sc, 1), M + 28, by + i * 40 * sc, 22 * sc, fg, bodyFont);
      });
    }
    return `${out}${footer(W, H, M, n, total, fg, bodyFont, sc)}</svg>`;
  }

  out += `<rect x="0" y="0" width="${W}" height="${H}" fill="${bg}"/>`;
  out += `<rect x="0" y="0" width="${r(14 * sc)}" height="${H}" fill="${primary}"/>`;
  const badge = type.toUpperCase();
  const bw = r((badge.length * 9 + 28) * sc);
  out += `<rect x="${M}" y="${r(34 * sc)}" width="${bw}" height="${r(26 * sc)}" rx="${r(13 * sc)}" fill="${primary}"/>`;
  out += `<text x="${r(M + bw / 2)}" y="${r(52 * sc)}" font-family="${esc(bodyFont)}" font-size="${r(13 * sc)}" font-weight="700" fill="${onColor(primary)}" text-anchor="middle">${esc(badge)}</text>`;
  const tl = wrap(title, W - 2 * M, 38 * sc * 0.95, 2);
  out += textBlock(tl, M, 108 * sc, 38 * sc, text, titleFont, 1.2, '700');
  let top = 108 * sc + tl.length * 38 * sc * 1.2 + 6 * sc;
  if (key) {
    const kl = wrap(key, W - 2 * M, 20 * sc, 2);
    out += textBlock(kl, M, top + 14 * sc, 20 * sc, muted, bodyFont, 1.35);
    top += kl.length * 20 * sc * 1.35 + 20 * sc;
  }
  top += 16 * sc;
  const bottom = H - 70 * sc;
  const areaW = W - 2 * M;
  const areaH = bottom - top;

  if (type === 'toc') {
    const items = bullets.length ? bullets : [title];
    const rows = Math.min(items.length, 8);
    const rh = Math.min(64 * sc, areaH / Math.max(rows, 1));
    items.slice(0, 8).forEach((item, i) => {
      const y = top + i * rh;
      out += `<rect x="${M}" y="${r(y)}" width="${r(areaW)}" height="${r(rh - 10 * sc)}" rx="10" fill="${surface}"/>`;
      out += `<text x="${r(M + 24 * sc)}" y="${r(y + (rh - 10 * sc) / 2 + 9 * sc)}" font-family="${esc(titleFont)}" font-size="${r(28 * sc)}" font-weight="700" fill="${primary}">${String(i + 1).padStart(2, '0')}</text>`;
      out += `<text x="${r(M + 90 * sc)}" y="${r(y + (rh - 10 * sc) / 2 + 8 * sc)}" font-family="${esc(bodyFont)}" font-size="${r(22 * sc)}" fill="${text}">${esc(item.slice(0, 80))}</text>`;
    });
  } else if (layout === 'kpi-grid' || layout === 'big-number' || (metrics.length && !bullets.length)) {
    const ms = metrics.length ? metrics : [{ label: key || title, value: '—', trend: '' }];
    const cnt = Math.min(ms.length, 6);
    let cols = layout === 'big-number' ? 1 : cnt <= 2 ? cnt : cnt === 4 ? 2 : 3;
    if (portrait) cols = Math.min(cols, 2);
    const rowsN = Math.ceil(cnt / cols);
    const gap = 24 * sc;
    const cw = (areaW - gap * (cols - 1)) / cols;
    const ch = Math.min(260 * sc, (areaH - (bullets.length ? 90 * sc : 0) - gap * (rowsN - 1)) / rowsN);
    ms.slice(0, 6).forEach((m, i) => {
      out += kpiCard(M + (i % cols) * (cw + gap), top + Math.floor(i / cols) * (ch + gap), cw, ch, String(m.value ?? ''), String(m.label ?? ''), String(m.trend ?? ''), k);
    });
    if (bullets.length) {
      const by = top + rowsN * (ch + gap) + 12 * sc;
      bullets.slice(0, 3).forEach((b, i) => {
        out += `<circle cx="${r(M + 7)}" cy="${r(by + i * 32 * sc - 6)}" r="5" fill="${primary}"/>`;
        out += textBlock(wrap(b, areaW - 30, 20 * sc, 1), M + 24, by + i * 32 * sc, 20 * sc, text, bodyFont);
      });
    }
  } else if (layout === 'three-card' || layout === 'four-quadrant') {
    let items = [...bullets];
    metrics.forEach((m) => items.push(`${m.label ?? ''}: ${m.value ?? ''}`.trim()));
    if (!items.length) items = [key || title];
    const q = layout === 'four-quadrant';
    const cnt = q ? 4 : 3;
    const cols = q ? 2 : portrait ? 1 : 3;
    const rowsN = Math.ceil(cnt / cols);
    const gap = 24 * sc;
    const cw = (areaW - gap * (cols - 1)) / cols;
    const ch = Math.min((areaH - gap * (rowsN - 1)) / rowsN, 300 * sc);
    for (let i = 0; i < cnt; i += 1) {
      const x = M + (i % cols) * (cw + gap);
      const y = top + Math.floor(i / cols) * (ch + gap);
      out += `<rect x="${r(x)}" y="${r(y)}" width="${r(cw)}" height="${r(ch)}" rx="14" fill="${surface}"/>`;
      out += `<rect x="${r(x)}" y="${r(y)}" width="${r(cw)}" height="${r(6 * sc)}" rx="3" fill="${primary}"/>`;
      out += `<circle cx="${r(x + 40 * sc)}" cy="${r(y + 52 * sc)}" r="${r(22 * sc)}" fill="${primary}"/>`;
      out += `<text x="${r(x + 40 * sc)}" y="${r(y + 60 * sc)}" font-family="${esc(titleFont)}" font-size="${r(22 * sc)}" font-weight="700" fill="${onColor(primary)}" text-anchor="middle">${i + 1}</text>`;
      const max = Math.max(3, Math.floor((ch - 110 * sc) / (22 * sc * 1.35)));
      out += textBlock(wrap(items[i] || '', cw - 56 * sc, 20 * sc, max), x + 28 * sc, y + 104 * sc, 20 * sc, text, bodyFont, 1.35);
    }
  } else if (layout === 'process-flow' || type === 'timeline') {
    const steps = bullets.length ? bullets : [key || title];
    const cnt = Math.min(steps.length, 6);
    const gap = 14 * sc;
    if (portrait) {
      const sh = (areaH - gap * (cnt - 1)) / cnt;
      steps.slice(0, 6).forEach((st, i) => {
        const y = top + i * (sh + gap);
        out += `<rect x="${M}" y="${r(y)}" width="${r(areaW)}" height="${r(sh)}" rx="12" fill="${surface}"/>`;
        out += `<rect x="${M}" y="${r(y)}" width="${r(10 * sc)}" height="${r(sh)}" rx="5" fill="${primary}"/>`;
        out += textBlock(wrap(st, areaW - 80 * sc, 20 * sc, 3), M + 36 * sc, y + 40 * sc, 20 * sc, text, bodyFont);
      });
    } else {
      const sw = (areaW - gap * (cnt - 1)) / cnt;
      const lineY = top + 54 * sc;
      out += `<line x1="${M}" y1="${r(lineY)}" x2="${r(W - M)}" y2="${r(lineY)}" stroke="${primary}" stroke-width="3" stroke-opacity="0.35"/>`;
      steps.slice(0, 6).forEach((st, i) => {
        const x = M + i * (sw + gap);
        out += `<circle cx="${r(x + 30 * sc)}" cy="${r(lineY)}" r="${r(26 * sc)}" fill="${primary}"/>`;
        out += `<text x="${r(x + 30 * sc)}" y="${r(lineY + 8 * sc)}" font-family="${esc(titleFont)}" font-size="${r(22 * sc)}" font-weight="700" fill="${onColor(primary)}" text-anchor="middle">${i + 1}</text>`;
        out += `<rect x="${r(x)}" y="${r(lineY + 50 * sc)}" width="${r(sw)}" height="${r(Math.max(bottom - lineY - 60 * sc, 20))}" rx="12" fill="${surface}"/>`;
        out += textBlock(wrap(st, sw - 32 * sc, 19 * sc, 7), x + 16 * sc, lineY + 90 * sc, 19 * sc, text, bodyFont, 1.35);
      });
    }
  } else if (layout === 'comparison-table' || type === 'comparison') {
    const half = Math.ceil(Math.max(bullets.length, 2) / 2);
    const gap = 28 * sc;
    const cw = (areaW - gap) / 2;
    [[bullets.slice(0, half), 0, primary, 'Option A'], [bullets.slice(half), 1, secondary, 'Option B']].forEach(([col, i, colr, lab]) => {
      const x = M + i * (cw + gap);
      out += `<rect x="${r(x)}" y="${r(top)}" width="${r(cw)}" height="${r(areaH)}" rx="14" fill="${surface}"/>`;
      out += `<rect x="${r(x)}" y="${r(top)}" width="${r(cw)}" height="${r(48 * sc)}" rx="14" fill="${colr}"/>`;
      out += `<text x="${r(x + 24 * sc)}" y="${r(top + 32 * sc)}" font-family="${esc(titleFont)}" font-size="${r(20 * sc)}" font-weight="700" fill="${onColor(colr)}">${lab}</text>`;
      col.forEach((b, row) => {
        const y = top + 84 * sc + row * 62 * sc;
        if (y > bottom - 30) return;
        out += `<circle cx="${r(x + 30 * sc)}" cy="${r(y - 6 * sc)}" r="6" fill="${colr}"/>`;
        out += textBlock(wrap(b, cw - 80 * sc, 20 * sc, 2), x + 50 * sc, y, 20 * sc, text, bodyFont);
      });
    });
  } else {
    const leftW = metrics.length || portrait ? (portrait ? areaW : areaW * 0.58) : areaW;
    let y = top + 6 * sc;
    (bullets.length ? bullets : [key || title]).slice(0, 6).forEach((b) => {
      const lines = wrap(b, leftW - 44 * sc, 22 * sc, 3);
      out += `<circle cx="${r(M + 9)}" cy="${r(y - 7 * sc)}" r="${r(6 * sc)}" fill="${primary}"/>`;
      out += textBlock(lines, M + 30 * sc, y, 22 * sc, text, bodyFont, 1.35);
      y += Math.max(1, lines.length) * 22 * sc * 1.35 + 24 * sc;
    });
    if (metrics.length && !portrait) {
      const px = M + leftW + 28 * sc;
      const pw = areaW - leftW - 28 * sc;
      const cnt = Math.min(metrics.length, 3);
      const ch = (areaH - 18 * sc * (cnt - 1)) / cnt;
      metrics.slice(0, 3).forEach((m, i) => {
        out += kpiCard(px, top + i * (ch + 18 * sc), pw, ch, String(m.value ?? ''), String(m.label ?? ''), String(m.trend ?? ''), k);
      });
    }
  }
  return `${out}${footer(W, H, M, n, total, muted, bodyFont, sc, primary)}</svg>`;
}
