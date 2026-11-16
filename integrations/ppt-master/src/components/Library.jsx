import { useEffect, useState } from 'react';
import { Alert, ConfirmModal, Spinner, Swatches, fmtDate } from './common.jsx';

export function Library({ api, config, onOpen }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [del, setDel] = useState(null);
  const brandOf = (id) => (config?.brands || []).find((b) => b.id === id);

  async function load() {
    setLoading(true); setError('');
    try { setData(await api.get('/projects')); } catch (e) { setError(e?.message || 'Could not load projects'); } finally { setLoading(false); }
  }
  useEffect(() => { load(); }, []);

  async function open(p) {
    setError('');
    try { const r = await api.get(`/projects/${encodeURIComponent(p.id)}`); onOpen(r.project); } catch (e) { setError(e?.message || 'Could not open project'); }
  }
  async function remove() {
    const p = del; setDel(null);
    try { await api.post('/projects/delete', { id: p.id }); await load(); } catch (e) { setError(e?.message || 'Could not delete project'); }
  }
  const card = (p) => (
    <div className="aik-card aik-stack aik-stack--gap-2" key={p.id}>
      <div className="aik-row aik-row--gap-2"><Swatches colors={brandOf(p.brand)?.colors} /><span className="aik-badge aik-badge--neutral">{p.status}</span></div>
      <strong>{p.topic || '(untitled)'}</strong>
      <span className="pptm-meta">{p.slide_count} slides · {p.canvas_format} · {fmtDate(p.updated_at)}</span>
      <div className="aik-row aik-row--gap-2">
        <button type="button" className="aik-btn aik-btn--secondary aik-btn--sm" onClick={() => open(p)}>Open</button>
        {!p.is_example ? <button type="button" className="aik-btn aik-btn--danger aik-btn--sm" onClick={() => setDel(p)}>Delete</button> : null}
      </div>
    </div>
  );
  return (
    <div className="aik-stack aik-stack--gap-3">
      <div className="aik-toolbar"><button type="button" className="aik-btn aik-btn--secondary aik-btn--sm" onClick={load}>Refresh</button></div>
      <Alert error={error} />
      {loading ? <Spinner /> : null}
      {data && !loading ? (
        <>
          <h3 className="aik-label">Your projects</h3>
          {data.projects?.length ? <div className="pptm-cards">{data.projects.map(card)}</div> : <div className="aik-empty"><h2 className="aik-empty__title">No projects yet</h2><p className="aik-empty__text">Plan a presentation in the Studio tab.</p></div>}
          <h3 className="aik-label">Examples</h3>
          {data.examples?.length ? <div className="pptm-cards">{data.examples.map(card)}</div> : <div className="aik-empty"><p className="aik-empty__text">No examples available.</p></div>}
        </>
      ) : null}
      {del ? <ConfirmModal title="Delete project?" body={`“${del.topic || del.id}” will be permanently removed.`} onConfirm={remove} onCancel={() => setDel(null)} /> : null}
    </div>
  );
}
