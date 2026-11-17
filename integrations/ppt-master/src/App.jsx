import { useEffect, useMemo, useRef, useState } from 'react';
import { utf8ToBase64 } from '@analyzer/integration-sdk';
import { Alert } from './components/common.jsx';
import { Brief } from './components/Brief.jsx';
import { Blueprint } from './components/Blueprint.jsx';
import { Viewer } from './components/Viewer.jsx';
import { ExportPanel } from './components/ExportPanel.jsx';
import { Library } from './components/Library.jsx';
import { Remote } from './components/Remote.jsx';
import { FALLBACK_CONFIG, normalizeConfig, normalizeProject, renderClientDeterministicSvg, synthesizeClientPlan } from './utils/clientFallbackEngine.js';

const ss = {
  get: (k, d = '') => { try { return sessionStorage.getItem(k) ?? d; } catch { return d; } },
  set: (k, v) => { try { if (v) sessionStorage.setItem(k, v); else sessionStorage.removeItem(k); } catch { /* ignore */ } },
};
const TABS = [['studio', 'Studio'], ['gallery', 'Slide Gallery & SVG Inspector'], ['library', 'Library'], ['remote', 'Remote Python Server Bridge']];
const STEPS = [[1, '1 Brief'], [2, '2 Blueprint'], [3, '3 Viewer'], [4, '4 Export']];
const EMPTY_GEN = { running: false, done: false, current: 0, total: 0, errors: [], fatal: '', latestSvg: '' };

/** Validate/unwrap every response so callers never see a string, a `{success:false}` body or a `{data}` envelope. */
function checkResponse(res) {
  let r = res;
  if (typeof r === 'string') {
    try { r = JSON.parse(r); } catch { throw new Error('Unexpected non-JSON response from the server (session expired, gateway block page or stray PHP output). Reload and try again.'); }
  }
  if (r === null || r === undefined) throw new Error('Empty response from the server.');
  if (r && typeof r === 'object') {
    if (r.success === false) {
      throw new Error([r.message, r.detail, r.error && r.error.message].filter(Boolean).join(' — ') || 'Request failed');
    }
    if (r.success === true && r.data && typeof r.data === 'object') return r.data;
  }
  return r;
}

function joinUrl(base, path) {
  const b = String(base || '').replace(/\/+$/, '');
  const p = String(path || '').replace(/^\/+/, '');
  return p ? `${b}/${p}` : b;
}

/**
 * POST `{payload: base64(json)}` (unsalted — the shape both the host DecodeEncodedPayload middleware and
 * PptMasterController::decoded() understand). The SDK's salted `{v,p}` envelope is rejected by the host gateway.
 */
async function sendPost(ctx, path, body, useEncryptHeader) {
  const url = joinUrl(ctx?.apiBase || '', path);
  const headers = {
    Accept: 'application/json',
    'Content-Type': 'application/json',
    'X-Requested-With': 'XMLHttpRequest',
  };
  if (useEncryptHeader) headers.encrypt = 'true';
  if (ctx?.csrfToken) headers['X-CSRF-TOKEN'] = ctx.csrfToken;
  const encoded = utf8ToBase64(JSON.stringify(body ?? {}));
  const res = await fetch(url, {
    method: 'POST',
    headers,
    credentials: 'same-origin',
    body: JSON.stringify({ payload: encoded }),
  });
  const text = await res.text();
  let data = null;
  if (text !== '') {
    try { data = JSON.parse(text); } catch { data = text; }
  }
  if (!res.ok) {
    const msg = (data && typeof data === 'object' && (data.message || (data.error && data.error.message) || data.error)) || res.statusText || 'Request failed';
    throw new Error(String(msg));
  }
  return data;
}

export function safeApi(raw, ctx) {
  const wrapCall = (fn) => async (...args) => checkResponse(await fn(...args));
  const post = async (path, body) => {
    // Attempt 1: encrypt header + unsalted payload. Attempt 2 (on block page / empty body): no encrypt header.
    let r = await sendPost(ctx, path, body, true);
    if (typeof r === 'string' || r === null || r === undefined) {
      r = await sendPost(ctx, path, body, false);
    }
    return r;
  };
  return {
    ...raw,
    get: wrapCall((...a) => raw.get(...a)),
    post: wrapCall(post),
    ...(typeof raw.postForm === 'function' ? { postForm: wrapCall((...a) => raw.postForm(...a)) } : {}),
  };
}

/** Find a usable project (with a slides array) in any plausible response shape. */
function extractProject(r) {
  if (!r || typeof r !== 'object') return null;
  const cand = [r.project, r.data && r.data.project, Array.isArray(r.slides) ? r : null];
  for (const c of cand) {
    const p = normalizeProject(c);
    if (p && p.slides.length > 0) return p;
  }
  return null;
}

export function App({ api: rawApi, ctx }) {
  const api = useMemo(() => safeApi(rawApi, ctx), [rawApi, ctx]);
  const [config, setConfig] = useState(FALLBACK_CONFIG);
  const [notice, setNotice] = useState('');
  const [cfgErr, setCfgErr] = useState('');
  const [tab, setTab] = useState('studio');
  const [step, setStep] = useState(1);
  const [model, setModel] = useState('');
  const [project, setProjectState] = useState(null);
  const [form, setForm] = useState({ topic: '', source_material: '', brand: ctx?.settings?.default_brand || 'rakuten-mobile', canvas_format: 'ppt169', slide_count: 8, structure_mode: 'briefing', auto_confirm: false });
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
        const c = normalizeConfig(await api.get('/config'));
        if (!mounted.current) return;
        setConfig(c);
        setModel(c.defaultModel || c.models?.[0]?.key || '');
        const def = c.settings?.default_brand || ctx?.settings?.default_brand || 'rakuten-mobile';
        const brand = c.brands.find((b) => b.id === def) ? def : c.brands.find((b) => b.id === 'none') ? 'none' : c.brands[0]?.id || 'none';
        setForm((f) => ({ ...f, brand }));
      } catch (e) {
        if (mounted.current) setCfgErr(`${e?.message || 'Could not load configuration'} — using built-in defaults.`);
      }
      const id = ss.get('pptm_project');
      if (id) {
        try {
          const proj = extractProject(await api.get(`/projects/${encodeURIComponent(id)}`));
          if (proj && mounted.current) { setProject(proj); setStep(proj.status === 'confirm_pending' ? 2 : Object.keys(proj.svgs).length ? 3 : 2); }
          else if (!proj) ss.set('pptm_project', '');
        } catch { ss.set('pptm_project', ''); }
      }
    })();
    return () => { mounted.current = false; cancelRef.current = true; };
  }, []);

  /** One slide: server first, deterministic client renderer on any failure. Never throws, always returns an SVG. */
  async function generateOne(p, s, customInstruction = '') {
    const total = (p?.slides || []).length;
    try {
      const r = await api.post('/executor/slide', {
        project_id: p.id, slide_number: s.slide_number, slide_plan: s, eight_confirmations: p.eight_confirmations, design_spec: p.design_spec,
        brand: p.brand, canvas_format: p.canvas_format, topic: p.topic, model, custom_instruction: customInstruction,
      });
      if (r && typeof r.svg === 'string' && r.svg.trim()) return { svg: r.svg, fallback: false, reason: '' };
      throw new Error('The server returned no SVG.');
    } catch (e) {
      return { svg: renderClientDeterministicSvg(s, p?.design_spec, p?.canvas_format, total, p?.topic), fallback: true, reason: e?.message || 'slide request failed' };
    }
  }

  async function generateAll(p0) {
    cancelRef.current = false;
    let p = normalizeProject(p0);
    if (!p) { setGen({ ...EMPTY_GEN, fatal: 'No project to generate.' }); return; }
    const slides = p.slides;
    setGen({ ...EMPTY_GEN, running: true, total: slides.length, current: 0 });
    const errors = [];
    let latest = '';
    let fallbackCount = 0;
    let lastReason = '';
    for (let i = 0; i < slides.length; i++) {
      if (cancelRef.current || !mounted.current) break;
      const s = slides[i];
      setGen((g) => ({ ...g, current: i }));
      try {
        const res = await generateOne(p, s);
        if (res.fallback) { fallbackCount += 1; lastReason = res.reason; }
        latest = res.svg || latest;
        p = { ...p, svgs: { ...p.svgs, [String(s.slide_number)]: res.svg } };
        if (!mounted.current) return;
        setProject(p);
        setGen((g) => ({ ...g, latestSvg: latest }));
      } catch (e) {
        errors.push(`Slide ${s.slide_number}: ${e?.message || 'generation failed'}`);
        setGen((g) => ({ ...g, errors: [...errors] }));
      }
    }
    if (!mounted.current) return;
    if (fallbackCount) setNotice(`${fallbackCount} of ${slides.length} slide(s) were rendered by the built-in offline renderer because the server request failed (${lastReason}).`);
    const cancelled = cancelRef.current;
    setGen((g) => ({ ...g, running: false, done: !cancelled, current: cancelled ? g.current : slides.length, errors }));
    if (!cancelled && Object.keys(p.svgs).length) setStep(3);
  }

  async function plan() {
    setBusy(true); setError(''); setNotice(''); setGen(EMPTY_GEN);
    let proj = null;
    let reason = '';
    try {
      const r = await api.post('/strategist/plan', { ...form, model, ...(remoteUrl ? { remote_url: remoteUrl } : {}) });
      proj = extractProject(r);
      if (!proj) reason = (r && typeof r === 'object' && (r.message || r.detail)) || 'The server returned an unexpected response (no project). The integration may have failed to load or your session may have expired.';
    } catch (e) { reason = e?.message || 'Planning request failed'; }
    try {
      if (!proj) {
        proj = normalizeProject(synthesizeClientPlan(form, config.brands));
        setNotice(`Server planning was unavailable (${reason}). An offline blueprint was generated in your browser instead — you can review and edit it as usual.`);
      }
      setProject(proj);
      setStep(2);
      if (form.auto_confirm) {
        setBusy(false);
        await confirmAndGenerate(proj);
      }
    } catch (e) { setError(e?.message || 'Planning failed'); } finally { setBusy(false); }
  }

  async function confirmAndGenerate(p) {
    const target = normalizeProject(p && Array.isArray(p.slides) ? p : projRef.current);
    if (!target || target.slides.length === 0) {
      setError('There is no blueprint to confirm yet. Plan a presentation first or add at least one slide.');
      return;
    }
    try {
      setError('');
      setGen({ ...EMPTY_GEN, running: true, total: target.slides.length });
      let np = { ...target, status: 'confirmed' };
      try {
        const r = await api.post('/strategist/confirm', { id: target.id, topic: target.topic, eight_confirmations: target.eight_confirmations, design_spec: target.design_spec, slides: target.slides });
        np = extractProject(r) || np;
      } catch (e) {
        setNotice(`Could not save the confirmation on the server (${e?.message || 'unknown error'}); continuing locally.`);
      }
      np = { ...np, svgs: {} };
      setProject(np);
      await generateAll(np);
    } catch (e) {
      setGen({ ...EMPTY_GEN, fatal: e?.message || 'Confirmation failed' });
    }
  }

  function openProject(raw) {
    const p = normalizeProject(raw);
    if (!p) { setError('That project could not be opened (no project data returned).'); return; }
    setProject(p);
    setGen(EMPTY_GEN);
    setError('');
    setStep(p.status === 'confirm_pending' ? 2 : Object.keys(p.svgs).length ? 3 : 2);
    setTab('studio');
  }
  function newProject() { projRef.current = null; setProjectState(null); ss.set('pptm_project', ''); setGen(EMPTY_GEN); setStep(1); setError(''); setNotice(''); }

  const brand = (config?.brands || []).find((b) => b.id === (project?.brand || form.brand));
  const remote = config?.remoteBackend;
  // A step beyond the Brief without a project can never render: fall back to the Brief instead of a blank tab.
  const shownStep = !project && step > 1 ? 1 : step;

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
            {!(config?.models || []).length ? <option value="">Analyzer default</option> : null}
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
      {tab === 'remote' ? null : <Alert error={cfgErr} title="Configuration problem" variant="warning" />}
      <Alert error={notice} title="Notice" variant="warning" />
      <Alert error={error} title="Could not complete the request" />

      {tab === 'studio' ? (
        <>
          <div className="aik-row aik-row--gap-2" style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}>
            <div className="pptm-steps">
              {STEPS.map(([n, l]) => (
                <button type="button" key={n} className="pptm-step" aria-current={shownStep === n} disabled={n > 1 && !project} onClick={() => setStep(n)}>{l}</button>
              ))}
            </div>
            {project ? <button type="button" className="aik-btn aik-btn--ghost aik-btn--sm" onClick={newProject} disabled={gen.running}>New presentation</button> : null}
          </div>
          {shownStep === 1 ? <Brief config={config} form={form} setForm={setForm} onPlan={plan} busy={busy} /> : null}
          {shownStep === 2 && project ? <Blueprint project={project} setProject={setProject} gen={gen} onConfirm={() => confirmAndGenerate()} onCancel={() => { cancelRef.current = true; }} onSkip={() => setStep(3)} /> : null}
          {shownStep === 3 && project ? (
            <>
              <Viewer api={api} project={project} setProject={setProject} model={model} />
              <div className="aik-row aik-row--gap-2"><button type="button" className="aik-btn aik-btn--secondary" onClick={() => setStep(4)}>Continue to export</button></div>
            </>
          ) : null}
          {shownStep === 4 && project ? <ExportPanel project={project} /> : null}
        </>
      ) : null}

      {tab === 'gallery' ? (project ? <Viewer api={api} project={project} setProject={setProject} model={model} gallery /> : <div className="aik-empty"><h2 className="aik-empty__title">No active project</h2><p className="aik-empty__text">Open a project from the Library or create one in the Studio.</p></div>) : null}
      {tab === 'library' ? <Library api={api} config={config} onOpen={openProject} /> : null}
      {tab === 'remote' ? <Remote api={api} config={config} status={remote} remoteUrl={remoteUrl} setRemoteUrl={setRemoteUrl} /> : null}
    </div>
  );
}
