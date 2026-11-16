# PPT Master (AI Analyzer integration)

AI presentation studio for the AI Analyzer platform.

- **Standalone native mode (default)**: Strategist (Eight Confirmations, design spec, slide blueprint) and Executor (one SVG per slide) run through the Analyzer AI engine (`ai()->chat`). If the model returns unusable output, a deterministic renderer guarantees every slide still renders.
- **Brand presets**: Rakuten Mobile, Rakuten Crimson, Anthropic, Google, Corporate Blue, Dark Tech, or auto.
- **Export (in the browser)**: editable native `.pptx` (pptxgenjs shapes + speaker notes), visual snapshot `.pptx`, SVG + spec `.zip`, project JSON.
- **Optional Remote Bridge**: set the `ppt_master_backend_url` setting (for example `http://host:8081`) to proxy jobs to an external PPT Master FastAPI server (`web/server.py`) and download its native `.pptx`.

## Settings
`default_model`, `default_brand`, `ppt_master_backend_url`, `max_tokens`.

## Build from source
```
npm install && SDK_JS_PATH=<kit>/sdk/js npm run build
make package NAME=ppt-master   # in aianalyzerkit
```
Projects are stored in the integration storage disk (`projects/*.json`).
