import { useEffect, useState } from 'react';
import { Alert, Progress, SvgImg, Spinner, PAGE_TYPES, LAYOUTS } from './common.jsx';

const EIGHT = [
  ['goal_and_type', 'Goal & Type'], ['audience_and_scenario', 'Audience & Scenario'], ['core_conclusion', 'Core Conclusion'], ['structure_mode', 'Structure Mode'],
  ['page_count', 'Page Count'], ['style_and_theme', 'Style & Theme'], ['aspect_ratio', 'Aspect Ratio'], ['content_outline_summary', 'Content Summary'],
];
const COLORS = ['primary', 'secondary', 'accent', 'background', 'surface', 'text'];

const metricsToText = (m) => (Array.isArray(m) ? m : []).map((x) => `${x?.label || ''} | ${x?.value || ''} | ${x?.trend || ''}`).join('\n');
const textToMetrics = (t) => t.split('\n').filter((l) => l.trim()).map((l) => { const [label = '', value = '', trend = ''] = l.split('|').map((x) => x.trim()); return { label, value, trend }; });
const hex6 = (c) => (/^#[0-9a-fA-F]{6}$/.test(c || '') ? c : '#000000');

function SlideRow({ s, i, total, onChange, onMove, onRemove }) {
  const [open, setOpen] = useState(false);
  const [bulletsText, setBulletsText] = useState((s.bullets || []).join('\n'));
  const [metricsText, setMetricsText] = useState(metricsToText(s.metrics));
  const up = (patch) => onChange({ ...s, ...patch });
  // S8: re-sync local text when the slide at this index changes (move / remove / external edit) so stale text is never written back.
  const bulletsKey = (s.bullets || []).join('\n');
  const metricsKey = metricsToText(s.metrics);
  useEffect(() => {
    setBulletsText((cur) => (cur.split('\n').filter((l) => l.trim()).join('\n') === bulletsKey ? cur : bulletsKey));
    setMetricsText((cur) => (metricsToText(textToMetrics(cur)) === metricsKey ? cur : metricsKey));
  }, [bulletsKey, metricsKey, s.slide_number, s.title]);
  return (
    <div className="pptm-slide">
      <div className="pptm-slide__head">
        <span className="aik-badge aik-badge--neutral">{i + 1}</span>
        <button type="button" className="pptm-slide__title" aria-expanded={open} onClick={() => setOpen(!open)}>{s.title || '(untitled)'}</button>
        <span className="aik-chip">{s.page_type}</span>
        <button type="button" className="aik-btn aik-btn--ghost aik-btn--sm" disabled={i === 0} onClick={() => onMove(-1)} aria-label="Move up">↑</button>
        <button type="button" className="aik-btn aik-btn--ghost aik-btn--sm" disabled={i === total - 1} onClick={() => onMove(1)} aria-label="Move down">↓</button>
        <button type="button" className="aik-btn aik-btn--ghost aik-btn--sm" onClick={onRemove} aria-label="Remove slide">Remove</button>
      </div>
      {open ? (
        <div className="pptm-slide__body">
          <div className="aik-field"><label className="aik-label">Title</label><input className="aik-input" value={s.title || ''} onChange={(e) => up({ title: e.target.value })} /></div>
          <div className="aik-field"><label className="aik-label">Subtitle</label><input className="aik-input" value={s.subtitle || ''} onChange={(e) => up({ subtitle: e.target.value })} /></div>
          <div className="aik-field"><label className="aik-label">Page type</label><select className="aik-select" value={s.page_type} onChange={(e) => up({ page_type: e.target.value })}>{PAGE_TYPES.map((p) => <option key={p}>{p}</option>)}</select></div>
          <div className="aik-field"><label className="aik-label">Layout pattern</label><select className="aik-select" value={s.layout_pattern} onChange={(e) => up({ layout_pattern: e.target.value })}>{LAYOUTS.map((p) => <option key={p}>{p}</option>)}</select></div>
          <div className="aik-field"><label className="aik-label">Key message</label><textarea className="aik-input pptm-textarea" value={s.key_message || ''} onChange={(e) => up({ key_message: e.target.value })} /></div>
          <div className="aik-field"><label className="aik-label">Bullets (one per line)</label><textarea className="aik-input pptm-textarea" value={bulletsText} onChange={(e) => { setBulletsText(e.target.value); up({ bullets: e.target.value.split('\n').filter((l) => l.trim()) }); }} /></div>
          <div className="aik-field"><label className="aik-label">Metrics (label | value | trend)</label><textarea className="aik-input pptm-textarea" value={metricsText} onChange={(e) => { setMetricsText(e.target.value); up({ metrics: textToMetrics(e.target.value) }); }} /></div>
          <div className="aik-field"><label className="aik-label">Visual notes</label><textarea className="aik-input pptm-textarea" value={s.visual_notes || ''} onChange={(e) => up({ visual_notes: e.target.value })} /></div>
          <div className="aik-field"><label className="aik-label">Speaker notes</label><textarea className="aik-input pptm-textarea" value={s.speaker_notes || ''} onChange={(e) => up({ speaker_notes: e.target.value })} /></div>
        </div>
      ) : null}
    </div>
  );
}

export function Blueprint({ project, setProject, gen: genIn, onConfirm, onCancel, onSkip }) {
  const ec = project?.eight_confirmations || {};
  const ds = { ...(project?.design_spec || {}), colors: project?.design_spec?.colors || {}, fonts: project?.design_spec?.fonts || {} };
  const slides = Array.isArray(project?.slides) ? project.slides : [];
  const gen = genIn || { running: false, done: false, current: 0, total: 0, errors: [], fatal: '', latestSvg: '' };
  const setEc = (k, v) => setProject({ ...project, eight_confirmations: { ...ec, [k]: v } });
  const setColor = (k, v) => setProject({ ...project, design_spec: { ...ds, colors: { ...ds.colors, [k]: v } } });
  const setFont = (k, v) => setProject({ ...project, design_spec: { ...ds, fonts: { ...ds.fonts, [k]: v } } });
  const renum = (arr) => arr.map((s, i) => ({ ...s, slide_number: i + 1 }));
  const setSlides = (arr) => setProject({ ...project, slides: renum(arr), slide_count: arr.length });
  const move = (i, d) => { const a = [...slides]; [a[i], a[i + d]] = [a[i + d], a[i]]; setSlides(a); };
  const add = () => setSlides([...slides, { slide_number: slides.length + 1, title: 'New slide', subtitle: '', page_type: 'content', layout_pattern: 'two-column', key_message: '', bullets: [], metrics: [], visual_notes: '', speaker_notes: '' }]);
  const latest = gen.latestSvg;

  return (
    <div className="aik-stack aik-stack--gap-3">
      <div className="aik-card aik-stack aik-stack--gap-3">
        <h2 className="aik-page__title" style={{ fontSize: 'var(--aik-font-size-lg)' }}>Step 2 · Eight Confirmations &amp; blueprint</h2>
        <p className="aik-help">Review and edit the strategist's plan for “{project?.topic || ''}”.</p>
        <div className="pptm-two">
          {EIGHT.map(([k, label]) => (
            <div className="aik-field" key={k}>
              <label className="aik-label" htmlFor={`pptm-ec-${k}`}>{label}</label>
              <textarea id={`pptm-ec-${k}`} className="aik-input pptm-textarea" value={ec[k] || ''} onChange={(e) => setEc(k, e.target.value)} disabled={gen.running} />
            </div>
          ))}
        </div>
      </div>

      <div className="aik-card aik-stack aik-stack--gap-3">
        <span className="aik-label">Palette &amp; fonts</span>
        <div className="pptm-palette">
          {COLORS.map((k) => (
            <label className="pptm-color" key={k}>
              <input type="color" value={hex6(ds.colors?.[k])} onChange={(e) => setColor(k, e.target.value)} disabled={gen.running} />
              <span>{k}</span>
            </label>
          ))}
        </div>
        <div className="pptm-two">
          <div className="aik-field"><label className="aik-label" htmlFor="pptm-tf">Title font</label><input id="pptm-tf" className="aik-input" value={ds.fonts?.title_font || ''} onChange={(e) => setFont('title_font', e.target.value)} /></div>
          <div className="aik-field"><label className="aik-label" htmlFor="pptm-bf">Body font</label><input id="pptm-bf" className="aik-input" value={ds.fonts?.body_font || ''} onChange={(e) => setFont('body_font', e.target.value)} /></div>
        </div>
      </div>

      <div className="aik-card aik-stack aik-stack--gap-2">
        <div className="aik-row aik-row--gap-2" style={{ justifyContent: 'space-between' }}>
          <span className="aik-label">Slide blueprint ({slides.length})</span>
          <button type="button" className="aik-btn aik-btn--secondary aik-btn--sm" onClick={add} disabled={gen.running}>Add slide</button>
        </div>
        {slides.map((s, i) => (
          <SlideRow key={i} s={s} i={i} total={slides.length} onChange={(ns) => setSlides(slides.map((x, j) => (j === i ? ns : x)))} onMove={(d) => move(i, d)} onRemove={() => setSlides(slides.filter((_, j) => j !== i))} />
        ))}
        {!slides.length ? <div className="aik-empty"><p className="aik-empty__text">No slides in the plan. Add one to continue.</p></div> : null}
      </div>

      {gen.running || gen.done || (gen.errors || []).length ? (
        <div className="aik-card aik-stack aik-stack--gap-2">
          <span className="aik-label">Generation {gen.running ? `· slide ${Math.min(gen.current + 1, gen.total)} of ${gen.total}` : ''}</span>
          <Progress value={gen.current} max={gen.total} />
          <div className="pptm-live">
            <SvgImg svg={latest} format={project?.canvas_format} alt="Latest generated slide" />
            <div className="aik-stack aik-stack--gap-1">
              {gen.running ? <Spinner label="Generating slides…" /> : null}
              {(gen.errors || []).map((e, i) => <Alert key={i} error={e} title="Slide failed" />)}
            </div>
          </div>
        </div>
      ) : null}

      <Alert error={gen.fatal} title="Could not generate" />
      <div className="aik-row aik-row--gap-2">
        {gen.running ? (
          <button type="button" className="aik-btn aik-btn--secondary" onClick={onCancel}>Cancel generation</button>
        ) : (
          <button type="button" className="aik-btn aik-btn--primary" disabled={!slides.length} onClick={onConfirm}>Confirm &amp; generate all slides</button>
        )}
        {!gen.running && Object.keys(project?.svgs || {}).length ? <button type="button" className="aik-btn aik-btn--secondary" onClick={onSkip}>View generated slides</button> : null}
      </div>
    </div>
  );
}
