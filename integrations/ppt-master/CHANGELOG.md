# Changelog

## 1.0.2
- Fix AI calls failing on Azure AI reasoning models (gpt-5.6-luna/sol/terra, gpt-6.1-sol): `temperature` is no longer sent; on error the call retries once with only `model`.
- Manifest aligned with reference integrations: `access: developer`, sidebar icon `FileText`.
- Client fallback model list updated to the Analyzer's live models (default `gpt-5.6-luna`).

## 1.0.1
- Fix "Could not plan presentation: Cannot read properties of undefined (reading 'slides')" and the blank Blueprint tab: responses are validated/unwrapped (`safeApi`), the step only advances once a project with slides exists, and errors/notices are shown on every step.
- Client-side resilience: built-in default brands/canvas formats, offline Strategist (`synthesizeClientPlan`) and deterministic SVG renderer when `/strategist/plan`, `/strategist/confirm` or `/executor/slide` fail.
- Backend: every action wrapped in try/catch, defensive payload decoding (`{v,p}` / `{payload}`), fault-tolerant `ProjectStore` (storage failures fall back to memory, invalid UTF-8 safe), project envelope also exposes top-level `slides`/`id`.
- Remote bridge: decode `confirm_data`, map `error_message`, populate `slide_count`, restrict `remote_url` (SSRF), fast 2s status probe.
- Manifest capabilities reduced to `["ai"]`; Blueprint slide rows no longer keep stale bullets/metrics after reorder/delete.

## 1.0.0
- Initial release: Strategist + Executor on the Analyzer AI engine, brand presets, SVG finalizer with deterministic fallback, project library, client-side PPTX/SVG exports, optional remote FastAPI bridge.
