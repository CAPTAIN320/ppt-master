import { useEffect, useState } from 'react';
import { downloadBlob } from '@analyzer/integration-sdk';
import { Alert, Spinner, SvgImg } from './common.jsx';

export function Viewer({ api, project, setProject, model, gallery }) {
  const slides = project.slides || [];
  const [idx, setIdx] = useState(0);
  const [instr, setInstr] = useState('');
  const [src, setSrc] = useState('');
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [info, setInfo] = useState(null);

  const cur = slides[Math.min(idx, Math.max(slides.length - 1, 0))];
  const n = cur ? cur.slide_number : 0;
  const svg = project.svgs?.[String(n)] || '';

  useEffect(() => { setSrc(svg); setNotes(cur?.speaker_notes || ''); setError(''); setInfo(null); }, [n, svg, cur?.speaker_notes]);

  if (!slides.length) return <div className="aik-empty"><h2 className="aik-empty__title">No slides yet</h2><p className="aik-empty__text">Plan and generate a presentation in the Studio tab first.</p></div>;

  async function run(label, fn) {
    setBusy(label); setError(''); setInfo(null);
    try { await fn(); } catch (e) { setError(e?.message || 'Request failed'); } finally { setBusy(''); }
  }
  const regenerate = () => run('regen', async () => {
    const r = await api.post('/executor/slide', {
      project_id: project.id, slide_number: n, slide_plan: cur, eight_confirmations: project.eight_confirmations, design_spec: project.design_spec,
      brand: project.brand, canvas_format: project.canvas_format, model, custom_instruction: instr,
    });
    setProject({ ...project, svgs: { ...project.svgs, [String(n)]: r.svg } });
  });
  const validate = () => run('val', async () => {
    const r = await api.post('/svg/finalize', { project_id: project.id, slide_number: n, svg: src });
    setProject({ ...project, svgs: { ...project.svgs, [String(n)]: r.svg || src } });
    setInfo({ valid: r.valid, warnings: r.warnings || [] });
  });
  const saveNotes = () => run('notes', async () => {
    const next = { ...project, slides: slides.map((s) => (s.slide_number === n ? { ...s, speaker_notes: notes } : s)) };
    const r = await api.post('/projects/save', { project: next });
    setProject(r.project || next);
    setInfo({ valid: true, warnings: [], saved: true });
  });

  const stage = (
    <div className="pptm-main">
      <SvgImg svg={svg} format={project.canvas_format} alt={`Slide ${n}`} />
      <div className="aik-row aik-row--gap-2">
        <button type="button" className="aik-btn aik-btn--secondary aik-btn--sm" disabled={idx <= 0} onClick={() => setIdx(idx - 1)}>Previous</button>
        <span className="pptm-meta">Slide {idx + 1} of {slides.length} · {cur.title}</span>
        <button type="button" className="aik-btn aik-btn--secondary aik-btn--sm" disabled={idx >= slides.length - 1} onClick={() => setIdx(idx + 1)}>Next</button>
        <button type="button" className="aik-btn aik-btn--ghost aik-btn--sm" disabled={!svg} onClick={() => downloadBlob(new Blob([svg], { type: 'image/svg+xml' }), `slide-${String(n).padStart(2, '0')}.svg`)}>Download SVG</button>
      </div>
      <div className="aik-field">
        <label className="aik-label" htmlFor="pptm-instr">Custom instruction</label>
        <div className="aik-row aik-row--gap-2">
          <input id="pptm-instr" className="aik-input" style={{ flex: 1 }} value={instr} onChange={(e) => setInstr(e.target.value)} placeholder="e.g. Use a bigger chart and less text" />
          <button type="button" className="aik-btn aik-btn--secondary" disabled={!!busy} onClick={regenerate}>Regenerate slide</button>
        </div>
      </div>
      {busy ? <Spinner label={busy === 'regen' ? 'Regenerating…' : 'Working…'} /> : null}
      <Alert error={error} />
      <div className="aik-field">
        <label className="aik-label" htmlFor="pptm-src-edit">Edit SVG source</label>
        <textarea id="pptm-src-edit" className="aik-input pptm-textarea pptm-textarea--mono" spellCheck={false} value={src} onChange={(e) => setSrc(e.target.value)} />
        <div className="aik-row aik-row--gap-2"><button type="button" className="aik-btn aik-btn--secondary" disabled={!!busy || !src.trim()} onClick={validate}>Validate &amp; save SVG</button></div>
        {info && !info.saved ? (
          <div className={`aik-alert aik-alert--${info.valid ? 'success' : 'warning'}`} role="status">
            <div className="aik-alert__title">{info.valid ? 'SVG is valid and saved' : 'SVG has issues'}</div>
            {info.warnings.length ? <ul className="pptm-warn">{info.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul> : null}
          </div>
        ) : null}
      </div>
      <div className="aik-field">
        <label className="aik-label" htmlFor="pptm-notes">Speaker notes</label>
        <textarea id="pptm-notes" className="aik-input pptm-textarea" value={notes} onChange={(e) => setNotes(e.target.value)} />
        <div className="aik-row aik-row--gap-2">
          <button type="button" className="aik-btn aik-btn--secondary aik-btn--sm" disabled={!!busy} onClick={saveNotes}>Save notes</button>
          {info?.saved ? <span className="aik-badge aik-badge--success">Saved</span> : null}
        </div>
      </div>
    </div>
  );

  return (
    <div className="aik-stack aik-stack--gap-3">
      {gallery ? (
        <div className="aik-card aik-stack aik-stack--gap-2">
          <span className="aik-label">Slide gallery</span>
          <div className="pptm-gallery">
            {slides.map((s, i) => (
              <button type="button" key={s.slide_number} className="pptm-thumb" aria-current={i === idx} onClick={() => setIdx(i)}>
                <SvgImg svg={project.svgs?.[String(s.slide_number)]} format={project.canvas_format} alt={`Slide ${s.slide_number}`} />
                {s.slide_number}. {s.title}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      <div className="aik-card">
        {gallery ? stage : (
          <div className="pptm-viewer">
            <div className="pptm-film" aria-label="Slides">
              {slides.map((s, i) => (
                <button type="button" key={s.slide_number} className="pptm-thumb" aria-current={i === idx} onClick={() => setIdx(i)}>
                  <SvgImg svg={project.svgs?.[String(s.slide_number)]} format={project.canvas_format} alt={`Slide ${s.slide_number}`} />
                  {s.slide_number}
                </button>
              ))}
            </div>
            {stage}
          </div>
        )}
      </div>
    </div>
  );
}
