import { useState } from 'react';
import { Alert, Spinner, Switch, DEFAULT_CANVASES, STRUCTURE_MODES } from './common.jsx';

export function Brief({ config, form, setForm, onPlan, busy, error }) {
  const [fileErr, setFileErr] = useState('');
  const brands = config?.brands || [];
  const canvases = config?.canvasFormats?.length ? config.canvasFormats : DEFAULT_CANVASES;
  const set = (k, v) => setForm({ ...form, [k]: v });

  function onFile(e) {
    const f = e.target.files?.[0];
    if (!f) return;
    setFileErr('');
    if (!/\.(txt|md|csv|json)$/i.test(f.name)) { setFileErr('Supported files: .txt, .md, .csv, .json'); return; }
    const r = new FileReader();
    r.onload = () => setForm({ ...form, source_material: (form.source_material ? form.source_material + '\n\n' : '') + String(r.result || '') });
    r.onerror = () => setFileErr('Could not read that file.');
    r.readAsText(f);
    e.target.value = '';
  }

  return (
    <div className="aik-stack aik-stack--gap-3">
      <div className="aik-card aik-stack aik-stack--gap-3">
        <h2 className="aik-page__title" style={{ fontSize: 'var(--aik-font-size-lg)' }}>Step 1 · Brief &amp; brand</h2>
        <div className="aik-field">
          <label className="aik-label" htmlFor="pptm-topic">Topic</label>
          <textarea id="pptm-topic" className="aik-input pptm-textarea" value={form.topic} onChange={(e) => set('topic', e.target.value)} placeholder="e.g. Q3 mobile growth strategy for the executive team" />
        </div>
        <div className="aik-field">
          <label className="aik-label" htmlFor="pptm-src">Source material (optional)</label>
          <textarea id="pptm-src" className="aik-input pptm-textarea" value={form.source_material} onChange={(e) => set('source_material', e.target.value)} placeholder="Paste notes, data or an article…" />
          <input type="file" accept=".txt,.md,.csv,.json" onChange={onFile} aria-label="Load source file" />
          <span className="aik-help">Files are read locally in your browser (.txt, .md, .csv, .json).</span>
          <Alert error={fileErr} title="File problem" variant="warning" />
        </div>
      </div>

      <div className="aik-card aik-stack aik-stack--gap-3">
        <span className="aik-label">Brand</span>
        <div className="pptm-cards">
          {brands.map((b) => (
            <button type="button" key={b.id} className="pptm-brand" aria-pressed={form.brand === b.id} onClick={() => set('brand', b.id)}>
              <span className="pptm-brand__name"><span className="pptm-dot" style={{ background: b.colors?.primary || 'transparent' }} />{b.name}</span>
              <span className="aik-help">{b.description}</span>
            </button>
          ))}
        </div>
        <div className="pptm-two">
          <div className="aik-field">
            <label className="aik-label" htmlFor="pptm-canvas">Canvas format</label>
            <select id="pptm-canvas" className="aik-select" value={form.canvas_format} onChange={(e) => set('canvas_format', e.target.value)}>
              {canvases.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
          </div>
          <div className="aik-field">
            <label className="aik-label" htmlFor="pptm-count">Slide count</label>
            <select id="pptm-count" className="aik-select" value={form.slide_count} onChange={(e) => set('slide_count', Number(e.target.value))}>
              {[5, 8, 10, 12].map((n) => <option key={n} value={n}>{n} slides</option>)}
            </select>
          </div>
          <div className="aik-field">
            <label className="aik-label" htmlFor="pptm-mode">Structure mode</label>
            <select id="pptm-mode" className="aik-select" value={form.structure_mode} onChange={(e) => set('structure_mode', e.target.value)}>
              {STRUCTURE_MODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
        </div>
        <Switch on={form.auto_confirm} onChange={(v) => set('auto_confirm', v)}>Auto-confirm (skip review and generate all slides)</Switch>
      </div>

      <Alert error={error} title="Could not plan presentation" />
      <div className="aik-row aik-row--gap-2">
        <button type="button" className={`aik-btn aik-btn--primary${busy ? ' aik-btn--loading' : ''}`} disabled={busy || !form.topic.trim()} onClick={onPlan}>Plan presentation</button>
        {busy ? <Spinner label="Planning…" /> : null}
      </div>
    </div>
  );
}
