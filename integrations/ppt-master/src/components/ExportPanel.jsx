import { useState } from 'react';
import { downloadBlob } from '@analyzer/integration-sdk';
import { exportEditablePptx, exportVisualPptx, exportSvgBundle, safeName } from '../utils/pptxExporter.js';
import { Alert, Progress } from './common.jsx';

export function ExportPanel({ project }) {
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const [prog, setProg] = useState([0, 0]);
  const hasSvgs = Object.keys(project.svgs || {}).length > 0;

  async function run(label, fn) {
    setBusy(label); setError(''); setNote(''); setProg([0, 0]);
    try { await fn(); } catch (e) { setError(e?.message || 'Export failed'); } finally { setBusy(''); }
  }
  return (
    <div className="aik-card aik-stack aik-stack--gap-3">
      <h2 className="aik-page__title" style={{ fontSize: 'var(--aik-font-size-lg)' }}>Step 4 · Export</h2>
      <div className="aik-row aik-row--gap-2" style={{ flexWrap: 'wrap' }}>
        <button type="button" className="aik-btn aik-btn--primary" disabled={!!busy || !(project.slides || []).length} onClick={() => run('editable', async () => downloadBlob(await exportEditablePptx(project), safeName(project, 'pptx')))}>Download editable .pptx</button>
        <button type="button" className="aik-btn aik-btn--secondary" disabled={!!busy || !hasSvgs} onClick={() => run('visual', async () => {
          const { blob, skipped } = await exportVisualPptx(project, (d, t) => setProg([d, t]));
          downloadBlob(blob, safeName(project, 'visual.pptx'));
          if (skipped.length) setNote(`Skipped slides that could not be rendered: ${skipped.join(', ')}`);
        })}>Download visual snapshot .pptx</button>
        <button type="button" className="aik-btn aik-btn--secondary" disabled={!!busy || !hasSvgs} onClick={() => run('zip', async () => downloadBlob(await exportSvgBundle(project), safeName(project, 'zip')))}>Download SVGs + spec (.zip)</button>
        <button type="button" className="aik-btn aik-btn--ghost" disabled={!!busy} onClick={() => downloadBlob(new Blob([JSON.stringify(project, null, 2)], { type: 'application/json' }), safeName(project, 'json'))}>Download project JSON</button>
      </div>
      {busy === 'visual' && prog[1] ? <Progress value={prog[0]} max={prog[1]} /> : null}
      {busy ? <span className="pptm-meta">Preparing export…</span> : null}
      <span className="aik-help">The editable export uses native PowerPoint shapes and text. The visual snapshot embeds each slide as a high-resolution image.</span>
      {note ? <Alert error={note} title="Note" variant="warning" /> : null}
      <Alert error={error} title="Export failed" />
    </div>
  );
}
