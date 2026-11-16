import JSZip from 'jszip';
import PptxGenJS from 'pptxgenjs';

const LAYOUTS = {
  ppt169: { w: 13.333, h: 7.5, px: [1280, 720] },
  ppt43: { w: 10, h: 7.5, px: [1024, 768] },
  xiaohongshu: { w: 7.5, h: 10, px: [1242, 1660] },
};

export const CANVAS_PIXELS = { ppt169: [1280, 720], ppt43: [1024, 768], xiaohongshu: [1242, 1660] };

const hex = (c, fallback) => {
  const v = String(c || '').replace('#', '').trim();
  return /^[0-9a-fA-F]{6}$/.test(v) ? v.toUpperCase() : fallback;
};

function layoutOf(project) {
  return LAYOUTS[project.canvas_format] || LAYOUTS.ppt169;
}

function newDeck(project) {
  const L = layoutOf(project);
  const pptx = new PptxGenJS();
  pptx.defineLayout({ name: 'PPTM', width: L.w, height: L.h });
  pptx.layout = 'PPTM';
  pptx.title = project.topic || 'Presentation';
  pptx.author = 'PPT Master';
  return { pptx, L };
}

export function safeName(project, ext) {
  const base = String(project.topic || project.id || 'presentation')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40);
  return `${base || 'presentation'}.${ext}`;
}

function buildSlide(pptx, L, project, s, total) {
  const col = project.design_spec?.colors || {};
  const fonts = project.design_spec?.fonts || {};
  const primary = hex(col.primary, '1F4E79');
  const secondary = hex(col.secondary, '2E75B6');
  const accent = hex(col.accent, 'F59E0B');
  const bg = hex(col.background, 'FFFFFF');
  const surface = hex(col.surface, 'F3F4F6');
  const text = hex(col.text, '222222');
  const muted = hex(col.muted, '777777');
  const titleFont = fonts.title_font || 'Arial';
  const bodyFont = fonts.body_font || 'Arial';
  const portrait = L.h > L.w;
  const m = portrait ? 0.5 : 0.6;
  const cw = L.w - m * 2;

  const slide = pptx.addSlide();
  slide.background = { color: bg };
  const isCover = s.page_type === 'cover' || s.page_type === 'closing' || s.layout_pattern === 'hero' || s.layout_pattern === 'closing';

  if (isCover) {
    slide.addShape('rect', { x: 0, y: 0, w: L.w, h: L.h, fill: { color: primary } });
    slide.addShape('rect', { x: m, y: L.h * 0.42, w: 1.2, h: 0.07, fill: { color: accent }, line: { color: accent } });
    slide.addText(s.title || '', { x: m, y: L.h * 0.2, w: cw, h: L.h * 0.22, fontFace: titleFont, fontSize: portrait ? 34 : 40, bold: true, color: 'FFFFFF', valign: 'bottom', fit: 'shrink' });
    slide.addText([s.subtitle, s.key_message].filter(Boolean).join('\n'), { x: m, y: L.h * 0.47, w: cw, h: L.h * 0.25, fontFace: bodyFont, fontSize: portrait ? 16 : 20, color: 'E5E7EB', valign: 'top', fit: 'shrink' });
  } else {
    const bh = portrait ? 1.3 : 1.15;
    slide.addShape('rect', { x: 0, y: 0, w: L.w, h: bh, fill: { color: primary }, line: { color: primary } });
    slide.addShape('rect', { x: 0, y: bh, w: L.w, h: 0.06, fill: { color: accent }, line: { color: accent } });
    slide.addText(s.title || '', { x: m, y: 0.1, w: cw, h: bh - 0.2, fontFace: titleFont, fontSize: portrait ? 24 : 28, bold: true, color: 'FFFFFF', valign: 'middle', fit: 'shrink' });
    let y = bh + 0.25;
    if (s.subtitle) {
      slide.addText(s.subtitle, { x: m, y, w: cw, h: 0.4, fontFace: bodyFont, fontSize: 16, color: secondary, bold: true, fit: 'shrink' });
      y += 0.5;
    }
    if (s.key_message) {
      slide.addShape('roundRect', { x: m, y, w: cw, h: 0.75, fill: { color: surface }, line: { color: surface }, rectRadius: 0.08 });
      slide.addText(s.key_message, { x: m + 0.15, y, w: cw - 0.3, h: 0.75, fontFace: bodyFont, fontSize: 15, italic: true, color: text, valign: 'middle', fit: 'shrink' });
      y += 0.95;
    }
    const metrics = (s.metrics || []).slice(0, 4);
    const footerY = L.h - 0.5;
    const metricsH = metrics.length ? 1.5 : 0;
    const bulletsH = Math.max(1, footerY - y - metricsH - 0.2);
    const bullets = (s.bullets || []).filter(Boolean);
    if (bullets.length) {
      slide.addText(
        bullets.map((b) => ({ text: String(b), options: { bullet: true, breakLine: true } })),
        { x: m, y, w: cw, h: bulletsH, fontFace: bodyFont, fontSize: portrait ? 15 : 18, color: text, valign: 'top', paraSpaceAfter: 6, fit: 'shrink' }
      );
    }
    if (metrics.length) {
      const gap = 0.25;
      const w = (cw - gap * (metrics.length - 1)) / metrics.length;
      metrics.forEach((mt, i) => {
        const x = m + i * (w + gap);
        const my = footerY - metricsH - 0.05;
        slide.addShape('roundRect', { x, y: my, w, h: metricsH, fill: { color: surface }, line: { color: secondary, width: 1 }, rectRadius: 0.1 });
        slide.addText(String(mt.value ?? ''), { x, y: my + 0.05, w, h: 0.75, align: 'center', valign: 'middle', fontFace: titleFont, fontSize: 28, bold: true, color: primary, fit: 'shrink' });
        slide.addText(String(mt.label ?? ''), { x, y: my + 0.8, w, h: 0.35, align: 'center', fontFace: bodyFont, fontSize: 12, color: text, fit: 'shrink' });
        if (mt.trend) slide.addText(String(mt.trend), { x, y: my + 1.12, w, h: 0.3, align: 'center', fontFace: bodyFont, fontSize: 11, bold: true, color: accent, fit: 'shrink' });
      });
    }
    slide.addText(String(project.topic || ''), { x: m, y: L.h - 0.45, w: cw - 1, h: 0.3, fontFace: bodyFont, fontSize: 10, color: muted });
    slide.addText(`${s.slide_number} / ${total}`, { x: L.w - m - 1, y: L.h - 0.45, w: 1, h: 0.3, align: 'right', fontFace: bodyFont, fontSize: 10, color: muted });
  }
  if (s.speaker_notes) slide.addNotes(String(s.speaker_notes));
}

export async function exportEditablePptx(project) {
  const { pptx, L } = newDeck(project);
  const slides = project.slides || [];
  slides.forEach((s, i) => {
    try {
      buildSlide(pptx, L, project, { ...s, slide_number: s.slide_number || i + 1 }, slides.length);
    } catch (e) {
      const fb = pptx.addSlide();
      fb.addText(`${s.title || 'Slide ' + (i + 1)}`, { x: 0.6, y: 0.6, w: L.w - 1.2, h: 1, fontSize: 28, bold: true });
    }
  });
  if (!slides.length) pptx.addSlide().addText(project.topic || 'Empty', { x: 0.6, y: 0.6, w: 8, h: 1, fontSize: 28 });
  return pptx.write({ outputType: 'blob' });
}

export function svgDataUrl(svg) {
  return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
}

function rasterize(svg, width, height) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      try {
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, width, height);
        ctx.drawImage(img, 0, 0, width, height);
        resolve(canvas.toDataURL('image/png'));
      } catch (e) {
        reject(e);
      }
    };
    img.onerror = () => reject(new Error('Could not render SVG'));
    img.src = svgDataUrl(svg);
  });
}

export async function exportVisualPptx(project, onProgress) {
  const { pptx, L } = newDeck(project);
  const [bw, bh] = L.px;
  const slides = project.slides || [];
  const nums = Object.keys(project.svgs || {}).map(Number).sort((a, b) => a - b);
  let done = 0;
  const skipped = [];
  for (const n of nums) {
    const svg = project.svgs[String(n)];
    if (!svg) continue;
    try {
      const data = await rasterize(svg, bw * 2, bh * 2);
      const slide = pptx.addSlide();
      slide.addImage({ data, x: 0, y: 0, w: L.w, h: L.h });
      const meta = slides.find((s) => Number(s.slide_number) === n);
      if (meta?.speaker_notes) slide.addNotes(String(meta.speaker_notes));
    } catch (e) {
      skipped.push(n);
    }
    done += 1;
    if (onProgress) onProgress(done, nums.length);
  }
  if (!nums.length) throw new Error('No generated SVG slides to export.');
  const blob = await pptx.write({ outputType: 'blob' });
  return { blob, skipped };
}

export function buildDesignSpecMd(project) {
  const ec = project.eight_confirmations || {};
  const ds = project.design_spec || {};
  const L = [];
  L.push(`# ${project.topic || 'Presentation'} - Design Spec`, '');
  L.push('## Eight Confirmations');
  [['goal_and_type', 'Goal & Type'], ['audience_and_scenario', 'Audience & Scenario'], ['core_conclusion', 'Core Conclusion'], ['structure_mode', 'Structure Mode'], ['page_count', 'Page Count'], ['style_and_theme', 'Style & Theme'], ['aspect_ratio', 'Aspect Ratio'], ['content_outline_summary', 'Content Summary']].forEach(([k, t]) => {
    L.push(`### ${t}`, String(ec[k] || ''), '');
  });
  L.push('## Palette');
  Object.entries(ds.colors || {}).forEach(([k, v]) => L.push(`- ${k}: ${v}`));
  L.push('', '## Fonts');
  Object.entries(ds.fonts || {}).forEach(([k, v]) => L.push(`- ${k}: ${v}`));
  L.push('', '## Slides');
  (project.slides || []).forEach((s) => {
    L.push(`### ${s.slide_number}. ${s.title || ''}`);
    L.push(`- page_type: ${s.page_type}`, `- layout_pattern: ${s.layout_pattern}`);
    if (s.subtitle) L.push(`- subtitle: ${s.subtitle}`);
    if (s.key_message) L.push(`- key message: ${s.key_message}`);
    (s.bullets || []).forEach((b) => L.push(`  - ${b}`));
    (s.metrics || []).forEach((m) => L.push(`- metric: ${m.label} | ${m.value} | ${m.trend || ''}`));
    if (s.visual_notes) L.push(`- visual notes: ${s.visual_notes}`);
    if (s.speaker_notes) L.push(`- speaker notes: ${s.speaker_notes}`);
    L.push('');
  });
  return L.join('\n');
}

export async function exportSvgBundle(project) {
  const zip = new JSZip();
  const svgs = project.svgs || {};
  Object.keys(svgs).forEach((k) => {
    if (svgs[k]) zip.file(`slide-${String(k).padStart(2, '0')}.svg`, svgs[k]);
  });
  zip.file('design_spec.md', buildDesignSpecMd(project));
  zip.file('project.json', JSON.stringify(project, null, 2));
  return zip.generateAsync({ type: 'blob' });
}
