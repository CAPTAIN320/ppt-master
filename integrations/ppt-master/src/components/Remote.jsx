import { useEffect, useRef, useState } from 'react';
import { downloadBlob } from '@analyzer/integration-sdk';
import { Alert, DEFAULT_CANVASES, SvgImg, Spinner, Switch, fmtDate } from './common.jsx';

const THEMES = [['rakuten-mobile', 'Rakuten Mobile'], ['rakuten-crimson', 'Rakuten Crimson'], ['none', 'None']];
const TERMINAL = ['done', 'error', 'failed'];

export function Remote({ api, config, status, remoteUrl, setRemoteUrl }) {
  const configured = !!status?.configured || !!remoteUrl;
  const extra = remoteUrl ? { remote_url: remoteUrl } : {};
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [sel, setSel] = useState(null);
  const [job, setJob] = useState(null);
  const [slides, setSlides] = useState({});
  const [confirmText, setConfirmText] = useState('');
  const [busy, setBusy] = useState('');
  const [form, setForm] = useState({ topic: '', theme: 'rakuten-mobile', canvas_format: 'ppt169', slide_count_pref: 8, auto_confirm: false, source_material: '' });
  const set = (k, v) => setForm({ ...form, [k]: v });
  const timer = useRef(null);
  const loadedConfirm = useRef(null);

  async function loadJobs() {
    setLoading(true);
    try { const r = await api.get('/remote/jobs', extra); setJobs(r.jobs || []); setError(''); } catch (e) { setError(e?.message || 'Could not load remote jobs'); } finally { setLoading(false); }
  }
  useEffect(() => { if (configured) loadJobs(); }, [configured, remoteUrl]);

  async function fetchJob(id) {
    try {
      const r = await api.get(`/remote/jobs/${encodeURIComponent(id)}`, extra);
      setJob(r.job);
      return r.job;
    } catch (e) { setError(e?.message || 'Could not load job'); return null; }
  }
  useEffect(() => {
    clearInterval(timer.current);
    if (!sel) { setJob(null); return undefined; }
    let alive = true;
    const tick = async () => {
      const j = await fetchJob(sel);
      if (!alive || !j) return;
      if (TERMINAL.includes(j.status)) clearInterval(timer.current);
    };
    tick();
    timer.current = setInterval(tick, 5000);
    return () => { alive = false; clearInterval(timer.current); };
  }, [sel, remoteUrl]);

  useEffect(() => {
    if (job?.status === 'confirm_pending' && loadedConfirm.current !== job.id) {
      loadedConfirm.current = job.id;
      setConfirmText(JSON.stringify(job.confirm_data ?? {}, null, 2));
    }
    if (job?.status === 'done') {
      const count = Number(job.slide_count) || (job.slides || []).length || 0;
      for (let n = 1; n <= count; n++) {
        if (slides[`${job.id}:${n}`] !== undefined) continue;
        slides[`${job.id}:${n}`] = '';
        api.get(`/remote/jobs/${encodeURIComponent(job.id)}/slides/${n}`, extra)
          .then((r) => setSlides((s) => ({ ...s, [`${job.id}:${n}`]: r.svg || '' }))).catch(() => {});
      }
    }
  }, [job?.status, job?.id]);

  async function create() {
    setBusy('create'); setError('');
    try {
      const r = await api.post('/remote/jobs', { ...form, model: '', ...extra });
      await loadJobs();
      setSel(r.job_id);
    } catch (e) { setError(e?.message || 'Could not create remote job'); } finally { setBusy(''); }
  }
  async function confirm() {
    setBusy('confirm'); setError('');
    try {
      let confirmations;
      try { confirmations = JSON.parse(confirmText); } catch { throw new Error('Confirmation JSON is not valid.'); }
      await api.post(`/remote/jobs/${encodeURIComponent(sel)}/confirm`, { confirmations, ...extra });
      loadedConfirm.current = null;
      await fetchJob(sel);
    } catch (e) { setError(e?.message || 'Could not confirm job'); } finally { setBusy(''); }
  }
  async function download() {
    setBusy('dl'); setError('');
    try {
      const r = await api.post(`/remote/jobs/${encodeURIComponent(sel)}/download`, { animation: 'none', ...extra });
      const bin = atob(r.base64 || '');
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      downloadBlob(new Blob([bytes], { type: r.mimeType || 'application/vnd.openxmlformats-officedocument.presentationml.presentation' }), r.filename || 'presentation.pptx');
    } catch (e) { setError(e?.message || 'Download failed'); } finally { setBusy(''); }
  }

  const canvases = config?.canvasFormats?.length ? config.canvasFormats : DEFAULT_CANVASES;
  const slideKeys = job ? Object.keys(slides).filter((k) => k.startsWith(`${job.id}:`)).sort((a, b) => Number(a.split(':')[1]) - Number(b.split(':')[1])) : [];

  return (
    <div className="aik-stack aik-stack--gap-3">
      <div className="aik-card aik-stack aik-stack--gap-2">
        <div className="aik-row aik-row--gap-2">
          <h2 className="aik-page__title" style={{ fontSize: 'var(--aik-font-size-lg)' }}>Remote Python server bridge</h2>
          <span className={`aik-badge aik-badge--${status?.reachable ? 'success' : configured ? 'warning' : 'neutral'}`}>{status?.reachable ? 'Connected' : configured ? 'Configured, not reachable' : 'Not configured'}</span>
        </div>
        {!configured ? (
          <div className="aik-alert aik-alert--info" role="status">
            <div className="aik-alert__title">Remote server not configured</div>
            <div className="aik-alert__body">The built-in Analyzer AI engine is used instead. The Studio tab works fully without a remote server. Enter a remote URL below to use one.</div>
          </div>
        ) : null}
        <div className="aik-field">
          <label className="aik-label" htmlFor="pptm-rurl">Remote URL (optional)</label>
          <input id="pptm-rurl" className="aik-input" value={remoteUrl} onChange={(e) => setRemoteUrl(e.target.value)} placeholder="http://localhost:8000" />
          <span className="aik-help">Remembered for this browser session only.</span>
        </div>
      </div>
      <Alert error={error} />
      {configured ? (
        <>
          <div className="aik-card aik-stack aik-stack--gap-2">
            <span className="aik-label">New remote job</span>
            <div className="aik-field"><label className="aik-label" htmlFor="pptm-rt">Topic</label><textarea id="pptm-rt" className="aik-input pptm-textarea" value={form.topic} onChange={(e) => set('topic', e.target.value)} /></div>
            <div className="aik-field"><label className="aik-label" htmlFor="pptm-rs">Source material</label><textarea id="pptm-rs" className="aik-input pptm-textarea" value={form.source_material} onChange={(e) => set('source_material', e.target.value)} /></div>
            <div className="pptm-two">
              <div className="aik-field"><label className="aik-label">Theme</label><select className="aik-select" value={form.theme} onChange={(e) => set('theme', e.target.value)}>{THEMES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
              <div className="aik-field"><label className="aik-label">Canvas</label><select className="aik-select" value={form.canvas_format} onChange={(e) => set('canvas_format', e.target.value)}>{canvases.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}</select></div>
              <div className="aik-field"><label className="aik-label">Slide count</label><select className="aik-select" value={form.slide_count_pref} onChange={(e) => set('slide_count_pref', Number(e.target.value))}>{[5, 8, 10, 12].map((n) => <option key={n} value={n}>{n}</option>)}</select></div>
            </div>
            <Switch on={form.auto_confirm} onChange={(v) => set('auto_confirm', v)}>Auto-confirm</Switch>
            <div className="aik-row aik-row--gap-2"><button type="button" className="aik-btn aik-btn--primary" disabled={busy === 'create' || !form.topic.trim()} onClick={create}>Create remote job</button></div>
          </div>

          <div className="aik-card aik-stack aik-stack--gap-2">
            <div className="aik-row aik-row--gap-2" style={{ justifyContent: 'space-between' }}>
              <span className="aik-label">Jobs</span>
              <button type="button" className="aik-btn aik-btn--secondary aik-btn--sm" onClick={loadJobs}>Refresh</button>
            </div>
            {loading ? <Spinner /> : null}
            {!loading && !jobs.length ? <div className="aik-empty"><p className="aik-empty__text">No remote jobs yet.</p></div> : null}
            {jobs.length ? (
              <div className="aik-table-wrap"><table className="aik-table"><thead><tr><th>Topic</th><th>Status</th><th>Slides</th><th>Created</th><th /></tr></thead><tbody>
                {jobs.map((j) => (
                  <tr key={j.id}><td>{j.topic}</td><td><span className="aik-badge aik-badge--neutral">{j.status}</span></td><td>{j.slide_count}</td><td>{fmtDate(j.created_at)}</td>
                    <td><button type="button" className="aik-btn aik-btn--ghost aik-btn--sm" onClick={() => { loadedConfirm.current = null; setSel(j.id); }}>{sel === j.id ? 'Selected' : 'Select'}</button></td></tr>
                ))}
              </tbody></table></div>
            ) : null}
          </div>

          {sel ? (
            <div className="aik-card aik-stack aik-stack--gap-2">
              <div className="aik-row aik-row--gap-2"><span className="aik-label">Job {sel}</span>{job ? <span className="aik-badge aik-badge--neutral">{job.status}</span> : <Spinner />}{job && !TERMINAL.includes(job.status) ? <Spinner label="Polling every 5s…" /> : null}</div>
              {job?.error ? <Alert error={String(job.error)} title="Job error" /> : null}
              {job?.status === 'confirm_pending' ? (
                <div className="aik-field">
                  <label className="aik-label" htmlFor="pptm-rc">Confirmation data (JSON)</label>
                  <textarea id="pptm-rc" className="aik-input pptm-textarea pptm-textarea--mono" spellCheck={false} value={confirmText} onChange={(e) => setConfirmText(e.target.value)} />
                  <div className="aik-row aik-row--gap-2"><button type="button" className="aik-btn aik-btn--primary" disabled={busy === 'confirm'} onClick={confirm}>Confirm job</button></div>
                </div>
              ) : null}
              {job?.status === 'done' ? (
                <>
                  <div className="pptm-gallery">{slideKeys.map((k) => <SvgImg key={k} svg={slides[k]} format={form.canvas_format} alt={`Remote slide ${k.split(':')[1]}`} />)}</div>
                  <div className="aik-row aik-row--gap-2"><button type="button" className="aik-btn aik-btn--primary" disabled={busy === 'dl'} onClick={download}>Download native .pptx</button></div>
                </>
              ) : null}
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
