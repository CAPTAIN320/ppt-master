import { useEffect, useRef, useState } from 'react';
import { Alert, Spinner } from './components/common.jsx';
import { Brief } from './components/Brief.jsx';
import { Blueprint } from './components/Blueprint.jsx';
import { Viewer } from './components/Viewer.jsx';
import { ExportPanel } from './components/ExportPanel.jsx';
import { Library } from './components/Library.jsx';
import { Remote } from './components/Remote.jsx';

const ss = {
  get: (k, d = '') => { try { return sessionStorage.getItem(k) ?? d; } catch { return d; } },
  set: (k, v) => { try { if (v) sessionStorage.setItem(k, v); else sessionStorage.removeItem(k); } catch { /* ignore */ } },
};
const TABS = [['studio', 'Studio'], ['gallery', 'Slide Gallery & SVG Inspector'], ['library', 'Library'], ['remote', 'Remote Python Server Bridge']];
const STEPS = [[1, '1 Brief'], [2, '2 Blueprint'], [3, '3 Viewer'], [4, '4 Export']];
const EMPTY_GEN = { running: false, done: false, current: 0, total: 0, errors: [], fatal: '', latestSvg: '' };

export function App({ api }) {
  const [config, setConfig] = useState(null);
  const [cfgErr, setCfgErr] = useState('');
  const [tab, setTab] = useState('studio');
  const [step, setStep] = useState(1);
  const [model, setModel] = useState('');
  const [project, setProjectState] = useState(null);
  const [form, setForm] = useState({ topic: '', source_material: '', brand: 'none', canvas_format: 'ppt169', slide_count: 8, structure_mode: 'briefing', auto_confirm: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [gen, setGen] = useState(EMPTY_GEN);
  const [remoteUrl, setRemoteUrlState] = useState(ss.get('pptm_remote_url'));
  const cancelRef = useRef(false);
  const projRef = useRef(null);
  const mounted = useRef(true);

  const setProject = (p) => { projRef.current = p; setProjectState(p); if (p?.id) ss.set('pptm_project', p.id); };
  const setRemoteUrl = (v) => { setRemoteUrlState(v); ss.set('pptm_remote_url', v); };

  useEffect(() => {
    mounted.current = true;
    (async () => {
      try {
        const c = await api.get('/config');
        setConfig(c);
        setModel(c.defaultModel || c.models?.[0]?.key || '');
        const brand = c.brands?.find((b) => b.id === 'none') ? 'none' : c.brands?.[0]?.id || 'none';
        setForm((f) => ({ ...f, brand }));
      } catch (e) { setCfgErr(e?.message || 'Could not load configuration'); }
      const id = ss.get('pptm_project');
      if (id) {
        try {
          const r = await api.get(`/projects/${encodeURIComponent(id)}`);
          if (mounted.current) { setProject(r.project); setStep(r.project.status === 'confirm_pending' ? 2 : r.project.slides?.length && Object.keys(r.project.svgs || {}).length ? 3 : 2); }
        } catch { ss.set('pptm_project', ''); }
      }
    })();
    return () => { mounted.current = false; cancelRef.current = true; };
  }, []);

  async function generateAll(p0, startFrom = 1) {
    cancelRef.current = false;
    let p = p0;
    const slides = p.slides || [];
    setGen({ ...EMPTY_GEN, running: true, total: slides.length, current: 0 });
    const errors = [];
    let latest = '';
    for (let i = 0; i < slides.length; i++) {
      if (cancelRef.current || !mounted.current) break;
      const s = slides[i];
      setGen((g) => ({ ...g, current: i }));
      try {
        const r = await api.post('/executor/slide', {
          project_id: p.id, slide_number: s.slide_number, slide_plan: s, eight_confirmations: p.eight_confirmations, design_spec: p.design_spec,
          brand: p.brand, canvas_format: p.canvas_format, model, custom_instruction: '',
        });
        latest = r.svg || latest;
        p = { ...p, svgs: { ...(p.svgs || {}), [String(s.slide_number)]: r.svg } };
        if (!mounted.current) return;
        setProject(p);
        setGen((g) => ({ ...g, latestSvg: latest }));
      } catch (e) {
        errors.push(`Slide ${s.slide_number}: ${e?.message || 'generation failed'}`);
        setGen((g) => ({ ...g, errors: [...errors] }));
      }
    }
    if (!mounted.current) return;
    const cancelled = cancelRef.current;
    setGen((g) => ({ ...g, running: false, done: !cancelled, current: cancelled ? g.current : slides.length, errors }));
    if (!cancelled && Object.keys(p.svgs || {}).length) setStep(3);
  }

  async function plan() {
    setBusy(true); setError(''); setGen(EMPTY_GEN);
    try {
      const r = await api.post('/strategist/plan', { ...form, model, ...(remoteUrl ? { remote_url: remoteUrl } : {}) });
      setProject(r.project);
      setStep(2);
      if (form.auto_confirm) {
        setBusy(false);
        await confirmAndGenerate(r.project);
      }
    } catch (e) { setError(e?.message || 'Planning failed'); } finally { setBusy(false); }
  }

  async function confirmAndGenerate(p = projRef.current) {
    setGen({ ...EMPTY_GEN, running: true, total: (p.slides || []).length });
    try {
      const r = await api.post('/strategist/confirm', { id: p.id, eight_confirmations: p.eight_confirmations, design_spec: p.design_spec, slides: p.slides });
      const np = r.project || p;
      setProject(np);
      await generateAll(np);
    } catch (e) {
      setGen({ ...EMPTY_GEN, fatal: e?.message || 'Confirmation failed' });
    }
  }

  function openProject(p) {
    setProject(p);
    setGen(EMPTY_GEN);
    setStep(p.status === 'confirm_pending' ? 2 : Object.keys(p.svgs || {}).length ? 3 : 2);
    setTab('studio');
  }
  function newProject() { projRef.current = null; setProjectState(null); ss.set('pptm_project', ''); setGen(EMPTY_GEN); setStep(1); setError(''); }

  const brand = config?.brands?.find((b) => b.id === (project?.brand || form.brand));
  const remote = config?.remoteBackend;

  return (
    <div className="aik-page pptm-app">
      <header className="pptm-header">
        <div className="pptm-header__left">
          <h1 className="pptm-title">PPT Master</h1>
          {brand ? <span className="aik-badge aik-badge--neutral">{brand.name}</span> : null}
        </div>
        <div className="aik-field">
          <label className="aik-label" htmlFor="pptm-model">AI model</label>
          <select id="pptm-model" className="aik-select pptm-model" value={model} onChange={(e) => setModel(e.target.value)} disabled={gen.running}>
            {(config?.models || []).map((m) => <option key={m.key} value={m.key}>{m.label}{m.provider ? ` (${m.provider})` : ''}</option>)}
          </select>
        </div>
      </header>

      <div className="aik-tabs" role="tablist" aria-label="Workspace">
        {TABS.map(([k, l]) => (
          <button type="button" role="tab" key={k} className="aik-tab" aria-selected={tab === k} onClick={() => setTab(k)}>
            {l}{k === 'remote' ? (remote?.reachable ? ' ●' : '') : ''}
          </button>
        ))}
      </div>
      {tab === 'remote' ? null : <Alert error={cfgErr} title="Configuration problem" />}
      {!config && !cfgErr ? <Spinner label="Loading configuration…" /> : null}

      {config && tab === 'studio' ? (
        <>
          <div className="aik-row aik-row--gap-2" style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}>
            <div className="pptm-steps">
              {STEPS.map(([n, l]) => (
                <button type="button" key={n} className="pptm-step" aria-current={step === n} disabled={n > 1 && !project} onClick={() => setStep(n)}>{l}</button>
              ))}
            </div>
            {project ? <button type="button" className="aik-btn aik-btn--ghost aik-btn--sm" onClick={newProject} disabled={gen.running}>New presentation</button> : null}
          </div>
          {step === 1 ? <Brief config={config} form={form} setForm={setForm} onPlan={plan} busy={busy} error={error} /> : null}
          {step === 2 && project ? <Blueprint project={project} setProject={setProject} gen={gen} onConfirm={() => confirmAndGenerate()} onCancel={() => { cancelRef.current = true; }} onSkip={() => setStep(3)} /> : null}
          {step === 3 && project ? (
            <>
              <Viewer api={api} project={project} setProject={setProject} model={model} />
              <div className="aik-row aik-row--gap-2"><button type="button" className="aik-btn aik-btn--secondary" onClick={() => setStep(4)}>Continue to export</button></div>
            </>
          ) : null}
          {step === 4 && project ? <ExportPanel project={project} /> : null}
        </>
      ) : null}

      {config && tab === 'gallery' ? (project ? <Viewer api={api} project={project} setProject={setProject} model={model} gallery /> : <div className="aik-empty"><h2 className="aik-empty__title">No active project</h2><p className="aik-empty__text">Open a project from the Library or create one in the Studio.</p></div>) : null}
      {config && tab === 'library' ? <Library api={api} config={config} onOpen={openProject} /> : null}
      {config && tab === 'remote' ? <Remote api={api} config={config} status={remote} remoteUrl={remoteUrl} setRemoteUrl={setRemoteUrl} /> : null}
    </div>
  );
}
