import { svgDataUrl, CANVAS_PIXELS } from '../utils/pptxExporter.js';

export const errMsg = (e) => (e && e.message ? String(e.message) : 'Something went wrong. Please try again.');

export const PAGE_TYPES = ['cover', 'toc', 'section', 'content', 'data', 'comparison', 'timeline', 'closing'];
export const LAYOUTS = ['hero', 'two-column', 'three-card', 'four-quadrant', 'kpi-grid', 'process-flow', 'comparison-table', 'big-number', 'closing'];
export const STRUCTURE_MODES = [
  ['briefing', 'Briefing'], ['pyramid', 'Pyramid / Executive'], ['showcase', 'Showcase'], ['narrative', 'Narrative'], ['instructional', 'Instructional'],
];
export const DEFAULT_CANVASES = [
  { id: 'ppt169', label: '16:9 (1280x720)', width: 1280, height: 720 },
  { id: 'ppt43', label: '4:3 (1024x768)', width: 1024, height: 768 },
  { id: 'xiaohongshu', label: '3:4 (1242x1660)', width: 1242, height: 1660 },
];

export function ratioOf(fmt) {
  const [w, h] = CANVAS_PIXELS[fmt] || CANVAS_PIXELS.ppt169;
  return `${w} / ${h}`;
}

export function Alert({ error, title = 'Error', variant = 'danger' }) {
  if (!error) return null;
  return (
    <div className={`aik-alert aik-alert--${variant}`} role="alert">
      <div className="aik-alert__title">{title}</div>
      <div className="aik-alert__body">{typeof error === 'string' ? error : errMsg(error)}</div>
    </div>
  );
}

export function Spinner({ label = 'Loading…' }) {
  return (
    <div className="aik-row aik-row--gap-2" role="status">
      <span className="aik-spinner" aria-hidden />
      <span>{label}</span>
    </div>
  );
}

export function SvgImg({ svg, format, alt }) {
  const style = { aspectRatio: ratioOf(format) };
  if (!svg) return <div className="pptm-stage pptm-stage--empty" style={style}>No preview</div>;
  return (
    <div className="pptm-stage" style={style}>
      <img src={svgDataUrl(svg)} alt={alt || 'Slide preview'} />
    </div>
  );
}

export function ConfirmModal({ title, body, confirmLabel = 'Delete', onConfirm, onCancel }) {
  return (
    <div className="aik-modal" role="dialog" aria-modal="true" aria-labelledby="pptm-confirm-title">
      <div className="aik-modal__backdrop" onClick={onCancel} />
      <div className="aik-modal__panel">
        <div className="aik-modal__header" id="pptm-confirm-title">{title}</div>
        <div className="aik-modal__body">{body}</div>
        <div className="aik-modal__footer">
          <button type="button" className="aik-btn aik-btn--secondary" onClick={onCancel}>Cancel</button>
          <button type="button" className="aik-btn aik-btn--danger" onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}

export function Swatches({ colors }) {
  const c = colors || {};
  return (
    <span className="pptm-swatches" aria-hidden>
      {['primary', 'secondary', 'accent', 'background'].map((k) => (
        <span key={k} className="pptm-dot" style={{ background: c[k] || 'transparent' }} />
      ))}
    </span>
  );
}

export function Progress({ value, max }) {
  const pct = max ? Math.round((value / max) * 100) : 0;
  return (
    <div className="pptm-progress" role="progressbar" aria-valuemin={0} aria-valuemax={max} aria-valuenow={value}>
      <div className="pptm-progress__bar" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Switch({ on, onChange, children }) {
  return (
    <button type="button" role="switch" aria-checked={on} className="aik-switch" onClick={() => onChange(!on)}>
      <span className="aik-switch__track" aria-hidden><span className="aik-switch__thumb" /></span>
      {children}
    </button>
  );
}

export const fmtDate = (s) => (s ? String(s).replace('T', ' ').slice(0, 16) : '');
