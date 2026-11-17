# AI Analyzer Integration Packaging Guide

> **Audience**: Human software engineers and agentic AI coding assistants (Kilo, Claude Code, Cursor, Herdr workers, etc.)  
> **Target Platform**: Internal AI Analyzer (`https://stg-ai-analyzer.rmb-lab.jp` / `http://aianalyzer.rmb-ss.jp`)  
> **SDK Version**: `AI Analyzer Kit 1.0.0.zip` (`analyzer/integration-sdk` `1.0.0`, Manifest Schema `1`)  
> **Reference Production Deployment**: `ppt-master` (`v1.0.0` → `v1.0.3`)

This guide is a complete, battle-tested reference for converting, building, validating, and packaging any application (including Python/FastAPI, Node.js, or React apps) into a deployable `.zip` integration for the **AI Analyzer** platform. Every rule, code template, and workaround in this document was verified against the SDK source and live staging/production behavior.

---

## Table of Contents

1. [Overview & Architecture of AI Analyzer Integrations](#1-overview--architecture-of-ai-analyzer-integrations)
2. [Exact Package Directory Layout & `Packager` Whitelist](#2-exact-package-directory-layout--packager-whitelist)
3. [Manifest (`integration.json`) Specification & Production Gotchas](#3-manifest-integrationjson-specification--production-gotchas)
4. [`PackageValidator` Static Analysis Rules (What Will Block Packaging/Boot)](#4-packagevalidator-static-analysis-rules-what-will-block-packagingboot)
5. [Critical Production Lessons Learned (The 4 Bugs That Break Integrations in Production & How to Prevent Them)](#5-critical-production-lessons-learned-the-4-bugs-that-break-integrations-in-production--how-to-prevent-them)
6. [Copy-Paste Starter Templates](#6-copy-paste-starter-templates)
7. [Fast Build, Validation & Canonical Zip Packaging Workflow (No Full Docker Harness Required)](#7-fast-build-validation--canonical-zip-packaging-workflow-no-full-docker-harness-required)

---

## 1. Overview & Architecture of AI Analyzer Integrations

### 1.1 What AI Analyzer Is
**AI Analyzer** is an internal Laravel 13 (PHP 8.3) host platform with an in-process plugin architecture called **Integrations**. Developers package integrations as `.zip` archives and upload them via **Integrations → Manage** (`/integrations`) on the Analyzer web UI.

When an integration is installed and enabled, the host's `Analyzer\IntegrationSdk\Runtime` dynamically:
1. Validates the extracted package directory using `PackageValidator::validateDirectory()`.
2. Registers a PSR-4 autoloader mapping `Integrations\<StudlyName>\` to the integration's `src/` directory.
3. Registers `Integrations\<StudlyName>\IntegrationProvider` with the Laravel container.
4. Mounts three route groups under `/integrations/{name}`:
   - **`/integrations/{name}/api/*`**: Loads `routes/api.php` behind middleware `['web', 'auth', 'integration.booted:{name}', 'integration.access:{name}']`.
   - **`/integrations/{name}/assets/{path}`**: Serves static build artifacts from `dist/` via `AssetController`.
   - **`/integrations/{name}/{path?}`**: Renders the host Blade shell, dynamically `import()`s the Vite entry module from `dist/`, and calls `mount(el, ctx)`.

### 1.2 What an Integration `.zip` Actually Is
An AI Analyzer integration `.zip` is **not** a container image or standalone microservice. It is a flat archive containing:
1. **`integration.json`**: Schema `1` manifest declaring metadata, capabilities, sidebar entry, settings, and Vite entry point.
2. **PHP Backend (`src/`, `routes/api.php`, `resources/`, optional `config/`)**: Laravel controllers and service classes that call host services through `Analyzer\IntegrationSdk\Integration::for('<name>')`:
   - `->ai()`: Chat completions (`chat($messages, $options)`), available models (`models()`), and default model (`defaultModel()`).
   - `->storage()`: Scoped `Illuminate\Contracts\Filesystem\Filesystem` disk for reading/writing integration data.
   - `->settings()`: Admin-configured key-value array defined in `integration.json`.
   - `->user()`: Authenticated user info (`['id', 'name', 'email', 'is_developer']`).
   - `->log()`: PSR-3 `LoggerInterface`.
   - `->path($rel)`: Absolute filesystem path inside the installed integration directory (used to read `resources/`).
3. **Prebuilt Vite ESM Bundle (`dist/` with `dist/.vite/manifest.json`)**: A self-contained JavaScript and CSS bundle (including React or any other UI library) that exports `{ mount(el, ctx) }` via `defineIntegration({ mount })`.

### 1.3 Key Architectural Constraint: In-Process Execution Only
Integrations run **in-process** inside the host's PHP-FPM workers and browser shell:
- **No Python, Node.js, or binary runtimes** exist inside the integration execution environment.
- **No Docker containers or background daemons** are spawned from the `.zip`.
- **All OS subprocess functions** (`exec`, `shell_exec`, `system`, `passthru`, `proc_open`, `popen`, `pcntl_*`, backticks `` `...` ``) and environment readers (`env()`, `getenv()`, `putenv()`, `$_ENV`, `$_SERVER`) are **strictly blocked** by `PackageValidator` static token scanning. Any single occurrence prevents packaging and prevents the integration from booting on the host.

### 1.4 Two Proven Patterns for Porting Existing Apps (e.g., Python/FastAPI/Node Apps)

When converting an existing full-stack or Python/Node application (such as `ppt-master` or `paper-writer`) into an AI Analyzer integration, use one of these two proven architectural patterns—or combine both as a hybrid:

#### Pattern A: Standalone Native + Browser Engine (Recommended)
Run 100% self-contained inside AI Analyzer with zero external infrastructure dependencies:
1. **Port Prompt & Business Orchestration to PHP (`src/Services/`)**:
   - Move system/user prompts, brand definitions, and static templates into `resources/` (e.g., `resources/prompts/*.md`, `resources/brands/*.md`) and load them via `Integration::for('<name>')->path('resources/...')`.
   - Replace OpenAI/Anthropic/LiteLLM Python SDK calls with `Integration::for('<name>')->ai()->chat($messages, $options)`.
   - Port deterministic post-processing (JSON repair, XML/SVG sanitization, LaTeX formatting, etc.) to pure PHP helper classes under `src/Support/`.
2. **Run Heavy File Generation & Rendering in the Browser ESM Bundle**:
   - Instead of calling Python `python-pptx`, `docx`, `reportlab`, or `weasyprint` on the server, bundle pure-JS equivalents directly into the Vite frontend (`pptxgenjs`, `jszip`, `docx`, `jspdf`, HTML5 `<canvas>` rasterization, ` SVG → PNG → PPTX`).
   - Trigger browser downloads using `downloadBlob(blob, filename)` from `@analyzer/integration-sdk`.
3. **Include a Deterministic Client-Side Fallback Engine (`clientFallbackEngine.js`)**:
   - Always bundle a deterministic client-side fallback generator in the frontend so that if an AI call times out, a gateway blocks a request, or the user is testing offline, the UI gracefully degrades with a warning banner (`aik-alert--warning`) and still produces a complete, valid output rather than crashing or hanging.

#### Pattern B: Thin PHP Proxy to an External Backend
If your application relies on heavy external compute (e.g., GPU inference, headless Chromium, or an existing FastAPI/Node cluster running on another server):
1. Keep the external Python/Node server running separately.
2. Add a setting in `integration.json` under `settings` (e.g., `"key": "backend_url"`, `"type": "string"`, `"default": ""`). Remember: `env()` and `getenv()` are forbidden by `PackageValidator`, so `Integration::for('<name>')->settings()['backend_url']` is the **only** valid way to configure external URLs.
3. Proxy requests from your PHP controller using Laravel's HTTP client (`Illuminate\Support\Facades\Http`):
   ```php
   use Illuminate\Support\Facades\Http;

   $baseUrl = rtrim(trim((string) (Integration::for('my-app')->settings()['backend_url'] ?? '')), '/');
   $response = Http::timeout(60)->acceptJson()->post($baseUrl.'/api/jobs', $payload);
   ```
4. **SSRF Protection**: Only allow requests to the admin-configured setting URL (or `localhost` / `127.0.0.1` / `host.docker.internal` for local dev). Never blindly proxy arbitrary user-supplied URLs in production.
5. **Fast Status Probes**: When checking if the external backend is reachable during `/config` initialization, use a short timeout (`Http::timeout(2)->get(...)`) wrapped in `try/catch (\Throwable)` so an unreachable external server never blocks the UI's first paint.

---

## 2. Exact Package Directory Layout & `Packager` Whitelist

### 2.1 Valid Integration Directory Tree

Below is the canonical directory structure for an integration during development (`integrations/<name>/`) and what gets staged into the final `.zip`:

```text
my-app/
├── integration.json                  # [SHIPPED] Required schema-1 manifest at zip root
├── README.md                         # [SHIPPED] Optional documentation
├── CHANGELOG.md                      # [SHIPPED] Optional release history
├── package.json                      # [DEV ONLY - excluded by Packager]
├── package-lock.json                 # [DEV ONLY - excluded by Packager]
├── vite.config.js                    # [DEV ONLY - excluded by Packager]
├── routes/
│   └── api.php                       # [SHIPPED] Mounted at /integrations/<name>/api/*
├── src/
│   ├── IntegrationProvider.php       # [SHIPPED] Required PSR-4 provider class
│   ├── Http/
│   │   └── Controllers/
│   │       └── MyAppController.php   # [SHIPPED] API route handlers
│   ├── Services/
│   │   ├── AiGateway.php             # [SHIPPED] Wrapper around Integration::for()->ai()
│   │   ├── ProjectStore.php          # [SHIPPED] Fault-tolerant storage wrapper
│   │   └── ...                       # [SHIPPED] Business logic (.php ONLY!)
│   ├── Support/
│   │   └── PromptRepository.php      # [SHIPPED] Reads static files from resources/
│   ├── main.jsx                      # [DEV ONLY - stripped from src/ by Packager!]
│   ├── App.jsx                       # [DEV ONLY - stripped from src/ by Packager!]
│   ├── styles.css                    # [DEV ONLY - stripped from src/ by Packager!]
│   └── components/                   # [DEV ONLY - stripped from src/ by Packager!]
├── resources/
│   ├── prompts/                      # [SHIPPED] System/user prompt templates (.md, .json, .txt)
│   └── brands/                       # [SHIPPED] Static presets, schemas, or assets
├── config/                           # [SHIPPED] Optional PHP config files
└── dist/                             # [SHIPPED] Prebuilt Vite output (required if frontend is set)
    ├── .vite/
    │   └── manifest.json             # [SHIPPED] Maps "src/main.jsx" -> hashed JS & CSS files
    └── assets/
        ├── main-XXXXXXXX.js          # [SHIPPED] Bundled ESM entry (includes React + deps)
        └── main-YYYYYYYY.css         # [SHIPPED] Bundled CSS (includes @analyzer/integration-sdk/ui.css)
```

### 2.2 What `Packager.php` Includes vs. Drops

`Analyzer\IntegrationSdk\Packager` enforces a strict whitelist (`shippableFiles()`):

| Location | `Packager` Rule | Crucial Implication |
| :--- | :--- | :--- |
| **Top-level files** | Only `integration.json`, `README.md`, `CHANGELOG.md` (`INCLUDE_TOP`). | `package.json`, `vite.config.js`, `.gitignore`, etc. are automatically omitted when staging via `Packager::stage()`. |
| **`src/`** | **ONLY files ending in `.php` are copied!** (`if ($dir === 'src' && !str_ends_with($file->getFilename(), '.php')) continue;`) |1. Frontend source files (`src/main.jsx`, `src/App.jsx`, `src/styles.css`) live in `src/` during development, get compiled into `dist/` by Vite, and are **automatically excluded** from the staged zip by `Packager`.<br>2. **Warning**: If you zip your directory manually without running `Packager::stage()`, leaving `.jsx`/`.js`/`.css` files inside `src/` triggers `PackageValidator` warnings (`non-php file under src/: ...`).<br>3. **Never** put backend `.json`, `.md`, or `.txt` data files inside `src/` expecting to read them at runtime from PHP—`Packager` will strip them! Put all non-PHP backend files in `resources/`. |
| **`routes/`** | All regular files (non-symlinks) are copied. | Contains `routes/api.php`. All `.php` files here are scanned by `PackageValidator`. |
| **`resources/`** | All regular files (non-symlinks) are copied. | Store prompts, markdown templates, JSON schemas, and static data here. Access via `Integration::for('<name>')->path('resources/...')`. |
| **`config/`** | All regular files (non-symlinks) are copied. | Optional configuration arrays. |
| **`dist/`** | All regular files (non-symlinks) are copied. | Must contain `.vite/manifest.json` (or `manifest.json`) and all referenced `.js` and `.css` assets. |
| **Forbidden Paths** | `vendor/`, `node_modules/`, `.git/`, `.env`, `.env.*`, symlinks, `.DS_Store`, `__MACOSX`. | Any of these in the extracted directory will cause `PackageValidator` or `SafeZip` to reject the package immediately. |

### 2.3 Zip Archive Limits & The Canonical File-Only Zip Rule (`SafeZip.php`)

`Analyzer\IntegrationSdk\SafeZip` enforces hard limits on every uploaded `.zip`:
- **Max `.zip` file size**: `50 MB` (configurable via host, default 50 MB)
- **Max single uncompressed file size**: `20 MB` (`SafeZip::MAX_FILE_BYTES = 20 * 1024 * 1024`)
- **Max total uncompressed size**: `100 MB` (`SafeZip::MAX_TOTAL_UNCOMPRESSED = 100 * 1024 * 1024`)
- **Max entry count**: `5000` (`SafeZip::MAX_ENTRIES = 5000`)
- **Path safety**: No NUL bytes, no leading `/`, no Windows drive letters (`C:`), no `..` traversal segments, and **no Unix symlinks** (`0120000` mode).

#### Critical Zip Entry Rule: 0 Directory Entries (`Packager::createZipFromDirectory`)
Look at how `Packager::createZipFromDirectory()` builds archives:
```php
foreach ($it as $file) {
    if (! $file->isFile()) {
        continue;
    }
    $relative = ltrim(substr($file->getPathname(), strlen($staging)), '/');
    $zip->addFile($file->getPathname(), $relative);
}
```
Notice two critical facts:
1. **`integration.json` is at the root of the zip** (no top-level wrapper folder like `my-app/integration.json`). While `SafeZip::detectWrapperPrefix()` attempts to strip a single wrapper prefix if every file is inside one, any stray root entry or inconsistent directory entry breaks wrapper detection. Always place `integration.json` directly at the zip root.
2. **Only regular files are added (`0` directory entries)**. Standard `zip -r out.zip .` adds `0`-byte `drwxr-xr-x` directory entries (`dist/`, `src/`, etc.). Always build **file-only zip archives** (either via `Packager::build()`, Python `zipfile` iterating `os.walk` files, or `zip -r -D`) and verify with `zipinfo` that every entry starts with `-rw` and no entry ends with `/`.

---

## 3. Manifest (`integration.json`) Specification & Production Gotchas

### 3.1 Annotated `integration.json` Template

```json
{
    "schema": 1,
    "name": "ppt-master",
    "displayName": "PPT Master",
    "version": "1.0.3",
    "description": "AI-powered presentation strategist and SVG-to-PPTX slide generator with Rakuten brand presets, Eight Confirmations workflow, and native editable PowerPoint export.",
    "author": "PPT Master Team",
    "sdk": "^1.0",
    "access": "developer",
    "capabilities": [
        "ai"
    ],
    "frontend": {
        "entry": "src/main.jsx"
    },
    "sidebar": [
        {
            "label": "PPT Master",
            "path": "",
            "icon": "FileText",
            "position": 30
        }
    ],
    "settings": [
        {
            "key": "default_model",
            "type": "string",
            "label": "Default AI model (blank uses Analyzer default)",
            "default": ""
        },
        {
            "key": "default_brand",
            "type": "string",
            "label": "Default brand preset (rakuten-mobile, rakuten-crimson, anthropic, google, corporate-blue, none)",
            "default": "rakuten-mobile"
        },
        {
            "key": "ppt_master_backend_url",
            "type": "string",
            "label": "Optional external PPT Master FastAPI server URL (blank uses built-in Analyzer AI engine)",
            "default": ""
        },
        {
            "key": "max_tokens",
            "type": "string",
            "label": "Max tokens per AI generation call",
            "default": "8192"
        }
    ]
}
```

### 3.2 Field-by-Field Reference & Production Gotchas

| Field | Type / Constraint | Detailed Rules & Production Gotchas |
| :--- | :--- | :--- |
| `schema` | Integer `1` | **Required.** Must be integer `1` (`($data['schema'] ?? null) !== 1` throws `ManifestException`). |
| `name` | `^[a-z][a-z0-9-]{1,39}$` | **Required.** Kebab-case slug. Cannot be any reserved word: `sdk`, `assets`, `api`, `admin`, `menu`, `catalog`, `index`.<br>**PSR-4 Namespace Rule**: The PHP namespace is derived via `Manifest::studly($name)`, which runs `Str::studly(str_replace('-', ' ', $name))`. For example:<br>• `ppt-master` → `Integrations\PptMaster\`<br>• `my-app` → `Integrations\MyApp\`<br>Your `src/IntegrationProvider.php` **must** declare `namespace Integrations\<StudlyName>;` and every `.php` file under `src/` **must** declare a namespace starting with `Integrations\<StudlyName>`. |
| `displayName` | Non-empty string | **Required.** Human-readable title shown in the Analyzer Integrations hub and header. |
| `version` | Strict SemVer (`x.y.z[-pre]`) | **Required.** Validated by `Sdk::isValidSemver()` (e.g., `1.0.0`, `1.0.1`, `1.0.2`).<br>**UPGRADE GOTCHA**: Look at `Installer::upgrade()`:<br>`if (!$force && Sdk::compareSemver($newVersion, $oldVersion) <= 0) throw new \RuntimeException('upgrade version must be greater than installed unless force');`<br>If `1.0.1` is currently installed on Analyzer and you upload a modified zip that still says `"version": "1.0.1"`, **the upload will fail** unless you remove the integration first or bump the version to `1.0.2`! Always increment `version` on every rebuild, or generate versioned zips (`1.0.0`, `1.0.1`, `1.0.2`, `1.0.3`) so any target state can upgrade cleanly. |
| `description` | String | Concise summary shown in the Integrations catalog card. |
| `author` | String | Team or developer name. |
| `sdk` | Composer constraint (`"^1.0"`) | **Required.** Checked via `Sdk::satisfies(Sdk::VERSION, $manifest->sdkConstraint())` against host SDK `1.0.0`. Always use `"^1.0"`. |
| `access` | `"developer"` or `"authenticated"` | Use **`"developer"`** by default to match the kit's reference integrations (`hello-world` and `paper-writer`). Only `"developer"` and `"authenticated"` are accepted by `Manifest::validateSchema()`. |
| `capabilities` | Array of strings | Use **`["ai"]`** (matching `hello-world` and `paper-writer`). Avoid inventing or adding unverified capability strings that the host UI or policy engine might reject. Note that `Integration::for('<name>')->storage()` works without needing `"storage"` in `capabilities`, and wrapping `storage()` in `try/catch` with an in-memory fallback guarantees safety either way. |
| `frontend` | `{"entry": "src/main.jsx"}` | Must match the exact input key in `dist/.vite/manifest.json`. `ViteManifestReader::resolve('src/main.jsx')` looks up `$manifest['src/main.jsx']['file']` and `['css']` and verifies that every referenced file exists under `dist/`. |
| `sidebar` | Array of objects | Each entry has `label` (string), `path` (`""` for root), `icon` (string), and `position` (integer).<br>**ICON GOTCHA**: The host shell maps `icon` to its internal icon set. Unrecognized icon names (such as `"Presentation"`) may not exist in the host's icon map. Stick to **known-good icon names** used by the official kit: **`"FileText"`** (used in `paper-writer` and SDK stubs) or **`"Sparkles"`** (used in `hello-world`). |
| `settings` | Array of objects | Admin-configurable settings (`key`, `type`, `label`, `default`). On initial install (`Installer::install()`), `defaultSettings()` populates `registry.json` with each setting's `default`. On `Installer::upgrade()`, existing admin-configured settings in `registry.json` are preserved. |

### 3.3 The Post-Upload "Enable" Gotcha (Why Nothing Shows Up After First Upload)

Look at `Installer::install()` in `sdk/src/Installer.php`:
```php
$this->registry->put($name, [
    'enabled' => false,
    'version' => $report->manifest['version'],
    'installed_at' => gmdate('c'),
    'installed_by' => $actor,
    'checksum' => $report->checksum,
    'settings' => $this->defaultSettings(Manifest::fromArray($report->manifest)),
]);
```
And look at `Runtime::boot()` and `Runtime::registerRoutes()` in `sdk/src/Runtime.php`:
```php
foreach ($this->registry->all() as $name => $entry) {
    if (! ($entry['enabled'] ?? false)) {
        continue;
    }
    $this->bootIntegration($name, $entry);
}
```
**What this means in production**:
1. When you upload a brand-new integration `.zip` for the first time, it is registered with **`"enabled": false`**!
2. While `"enabled": false`, `Runtime::boot()` and `Runtime::registerRoutes()` **completely skip it**:
   - It will **not** appear in the sidebar.
   - Navigating to `/integrations/<name>` or `/integrations/<name>/api/*` returns **HTTP 404**.
3. **Action Required After First Install**: Immediately after uploading a new integration in **Integrations → Manage** (`/integrations`), click **Enable** on the integration's row and refresh the page. (Subsequent upgrades via `Installer::upgrade()` preserve the `enabled` flag.)

---

## 4. `PackageValidator` Static Analysis Rules (What Will Block Packaging/Boot)

`Analyzer\IntegrationSdk\PackageValidator` runs both at build/package time **and** at runtime inside `Runtime::bootIntegration()` before your integration is allowed to boot. If `validateDirectory()` returns even a single error, your integration fails to install or fails to boot.

### 4.1 Complete Table of Static Analysis & Layout Rules

| Category | Forbidden / Required Pattern | Exact Validator Behavior (`PackageValidator.php`) | How to Comply |
| :--- | :--- | :--- | :--- |
| **Forbidden Process / Shell Functions** | `exec`, `shell_exec`, `system`, `passthru`, `proc_open`, `popen`, `pcntl_exec`, `pcntl_fork`, `pcntl_signal`, `pcntl_wait`, `pcntl_wexitstatus`, `posix_kill` | **ERROR**: `forbidden function <name>` | Never spawn subprocesses. Port logic to PHP or browser JS, or use `Illuminate\Support\Facades\Http` to call an external service. |
| **Forbidden Eval / Dynamic Code** | `eval(...)`, `create_function(...)`, `dl(...)`, `assert('string')`, backtick operator `` `...` `` | **ERROR**: `eval language construct is forbidden` / `backtick operator is forbidden` / `assert() with string argument is forbidden` | Use standard PHP functions and closures. |
| **Forbidden Environment Access** | `env(...)`, `getenv(...)`, `putenv(...)`, `$_ENV`, `$_SERVER` | **ERROR**: `forbidden call env()` / `forbidden function getenv` / `$_ENV access is forbidden` / `$_SERVER access is forbidden` | **Never** read environment variables or `$_SERVER`. Use `$request->header(...)`, `$request->ip()`, and `Integration::for('<name>')->settings()`. |
| **Forbidden Host Namespaces** | `App\*`, `Libs\*` (any match of `/\b(App\|Libs)\\[A-Za-z0-9_\\]+/`) | **ERROR**: `reference to host namespace App\ (use SDK Host services)` | **Even in PHP comments or docblocks**, writing `App\Http\Middleware\...` or `App\Models\...` will trigger this regex! Never write `App\` or `Libs\` anywhere in `.php` files (even in comments). |
| **Database Facade Warning** | `Illuminate\Support\Facades\DB` | **WARNING**: `reference to Illuminate\Support\Facades\DB` | Avoid direct host DB queries. Use `Integration::for('<name>')->storage()` for persistence. |
| **Include / Require Rules** | `include`, `include_once`, `require`, `require_once` | **ERROR** if argument is a variable/expression (`dynamic include/require is forbidden`), starts with `/` (`include/require absolute path forbidden`), or resolves outside the package root. | Rely on PSR-4 autoloading for classes in `src/`. Do not use manual `require` or `include`. |
| **File I/O with Absolute Paths** | `file_put_contents('/...')`, `fopen('/...')`, `unlink('/...')` with a literal leading `/` | **WARNING**: `<func>() with absolute path` | Use `Integration::for('<name>')->storage()` or pass a variable from `Integration::for('<name>')->path('resources/...')` to `file_get_contents($path)`. |
| **Allowed PHP Directories** | Only `src/`, `routes/`, `config/`, `resources/` | **ERROR**: `php file outside allowed directories: <rel>` | Never place `.php` files at the root of the integration or in custom top-level folders. |
| **Namespace Enforcement** | Every `.php` file in `src/` | **ERROR**: `missing namespace declaration` or `namespace must be under Integrations\<Studly>\` | Every file under `src/` must have `namespace Integrations\<Studly>[...];`. |
| **Non-PHP Files Under `src/`** | Any non-`.php` file in `src/` | **WARNING**: `non-php file under src/: <rel>` | Always stage with `Packager::stage()` so `.jsx`/`.js`/`.css` files in `src/` are stripped before zipping. |
| **PHP Syntax Check** | `token_get_all(..., TOKEN_PARSE)` + `php -l` | **ERROR**: `syntax error in <rel>: ...` | Run `php -l` on all `.php` files using PHP 8.3 before packaging. |

### 4.2 Allowed HTTP Verbs Rule (`GET` and `POST` Only)
Per `AGENTS.md` and `docs/UI_GUIDELINES.md`:
- Use **`GET`** for read-only endpoints (`/config`, `/projects`, `/projects/{id}`).
- Use **`POST`** for **all** state-changing / mutation endpoints (`/projects/save`, `/projects/delete`, `/strategist/plan`, `/executor/slide`).
- **Never** register `PUT`, `PATCH`, or `DELETE` routes in `routes/api.php`—corporate proxies/WAFs and the SDK client (`createApi(ctx)` only exposes `get`, `post`, `postForm`) only support `GET` and `POST`.

---

## 5. Critical Production Lessons Learned (The 4 Bugs That Break Integrations in Production & How to Prevent Them)

These four production bugs were discovered while deploying `ppt-master` to `https://stg-ai-analyzer.rmb-lab.jp` and `http://aianalyzer.rmb-ss.jp`. **Every integration must implement all four fixes below.**

---

### Lesson 1: The `POST` Payload Encoding Mismatch (`ProbePayloadGuard` / F5 WAF vs. `sdk/js/index.js`)

#### The Symptom
- `GET /integrations/<name>/api/config` succeeds (`200 OK` JSON).
- Every `POST /integrations/<name>/api/*` request fails in the browser, or returns **HTTP 200 OK** with an HTML error page:
  ```html
  <HTML><HEAD><TITLE>Error occurred/エラーが発生しました</TITLE>...
  <td>Reference Number/リファレンス番号：</td><td>707823452384319560</td>
  ```
- Because the HTTP status code is `200 OK`, `fetch()` does not reject (`res.ok === true`), and `sdk/js/index.js`'s `parseResponse()` catches the `JSON.parse()` failure and returns the **raw HTML string** as the resolved value of `await api.post(...)`!

#### The Root Cause
There is a wire-protocol mismatch between `sdk/js/index.js` in `AI Analyzer Kit 1.0.0.zip` and the actual middleware running on `stg-ai-analyzer.rmb-lab.jp`:
1. **`sdk/js/index.js` (`createApi(ctx).post`)** encodes POST bodies with `encodeJsonBody(body)`:
   ```js
   // What sdk/js/index.js sends:
   headers: { encrypt: 'true', 'Content-Type': 'application/json', ... }
   body: JSON.stringify({
     v: 1,
     p: utf8ToBase64("analyzer-encoded-payload-v1:" + JSON.stringify(body))
   })
   ```
2. **Host Middleware (`DecodeEncodedPayload` / `EncodedPayload` & `ProbePayloadGuard`)** and the upstream **F5 BIG-IP ASM WAF**:
   - Look at `harness/app/Http/Middleware/DecodeEncodedPayload.php` and `harness/app/Support/EncodedPayload.php` (which mirror `App\Support\EncodedPayload` in Analyzer):
     ```php
     public const FIELD = 'payload';
     // ...
     $encodedPayload = $request->input(EncodedPayload::FIELD);
     if (! is_string($encodedPayload) || trim($encodedPayload) === '') {
         return $this->invalidPayloadResponse('The payload field is required when encrypt header is true.');
     }
     $decodedPayload = base64_decode($encodedPayload, true);
     $parsedPayload = json_decode($decodedPayload, true); // Expects UNSALTED base64 JSON!
     ```
   - When `encrypt: true` is sent with `{"v": 1, "p": "..."}` (salted), `ProbePayloadGuard` / F5 WAF on `stg-ai-analyzer.rmb-lab.jp` rejects the request and returns an **HTTP 200 HTML block page** (`<TITLE>Error occurred/エラーが発生しました</TITLE>`), or `DecodeEncodedPayload` rejects it with `422 The payload field is required when encrypt header is true`.
   - Conversely, when `encrypt: true` is sent with **`{"payload": utf8ToBase64(JSON.stringify(body))}` (unsalted base64 in the `"payload"` key)**, both `ProbePayloadGuard` and `DecodeEncodedPayload` accept and decode it cleanly!

#### The Complete Fix (Frontend `safeApi` + Backend `decoded()` Helper)

Always wrap `createApi(ctx)` with `safeApi(rawApi, ctx)` on the frontend **and** call `$request = $this->decoded($request);` at the start of every `POST` controller action on the backend.

##### 1. Frontend: Copy-Pasteable `safeApi(raw, ctx)` Wrapper (`src/utils/safeApi.js` or `src/App.jsx`)

```javascript
import { utf8ToBase64 } from '@analyzer/integration-sdk';

/**
 * Validate and unwrap every API response so callers never receive:
 *  - an HTML block/login page returned with HTTP 200 (typeof res === 'string')
 *  - a boot/validation failure envelope returned with HTTP 200 ({ success: false, message, detail })
 *  - a wrapped { success: true, data: ... } envelope
 */
export function checkResponse(res) {
  let r = res;
  if (typeof r === 'string') {
    try {
      r = JSON.parse(r);
    } catch {
      throw new Error(
        'Unexpected non-JSON response from the server (session expired, gateway block page, or stray PHP output). Reload and try again.'
      );
    }
  }
  if (r === null || r === undefined) {
    throw new Error('Empty response from the server.');
  }
  if (r && typeof r === 'object') {
    if (r.success === false) {
      throw new Error(
        [r.message, r.detail, r.error && r.error.message].filter(Boolean).join(' — ') ||
          'Request failed'
      );
    }
    if (r.success === true && r.data && typeof r.data === 'object') {
      return r.data;
    }
  }
  return r;
}

function joinUrl(base, path) {
  const b = String(base || '').replace(/\/+$/, '');
  const p = String(path || '').replace(/^\/+/, '');
  return p ? `${b}/${p}` : b;
}

/**
 * Send POST with `{ payload: utf8ToBase64(JSON.stringify(body ?? {})) }` (unsalted).
 * Accepted by both host DecodeEncodedPayload/ProbePayloadGuard and controller decoded().
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
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const msg =
      (data &&
        typeof data === 'object' &&
        (data.message || (data.error && data.error.message) || data.error)) ||
      res.statusText ||
      'Request failed';
    throw new Error(String(msg));
  }
  return data;
}

/**
 * Drop-in wrapper around createApi(ctx) that fixes POST encoding and validates all responses.
 */
export function safeApi(raw, ctx) {
  const wrapCall = (fn) => async (...args) => checkResponse(await fn(...args));
  const post = async (path, body) => {
    // Attempt 1: encrypt: 'true' header + unsalted { payload: base64(json) }
    let r = await sendPost(ctx, path, body, true);
    // Attempt 2: If an upstream proxy returned an HTML block page (string) or empty body on encrypt: 'true',
    // retry without the encrypt header so the PHP controller's decoded() method decodes { payload } directly.
    if (typeof r === 'string' || r === null || r === undefined) {
      r = await sendPost(ctx, path, body, false);
    }
    return r;
  };
  return {
    ...raw,
    get: wrapCall((...a) => raw.get(...a)),
    post: wrapCall(post),
    ...(typeof raw.postForm === 'function'
      ? { postForm: wrapCall((...a) => raw.postForm(...a)) }
      : {}),
  };
}
```

##### 2. Backend: Copy-Pasteable `decoded(Request $request)` Controller Helper

Add this private method to your controller and call `$request = $this->decoded($request);` at the top of every `POST` handler. It handles:
- Requests already decoded by the host's `DecodeEncodedPayload` middleware (no-op).
- Requests sent with `{"payload": "<base64>"}` (with or without `encrypt: true`).
- Requests sent with `{"v": 1, "p": "<base64>"}` or `{"p": "<base64>"}` (salted or unsalted).
- Requests where `$request->all()` is empty due to `Content-Type` header quirks.

```php
/**
 * Decode {payload: base64(json)}, {v: 1, p: base64(salt+json)}, or {p: base64(json)}
 * when the host middleware did not decode it. No-op when already decoded.
 */
private function decoded(Request $request): Request
{
    try {
        $all = $request->all();
        $isEnvelope = static function ($a): bool {
            if (! is_array($a) || $a === []) {
                return false;
            }
            $k = array_keys($a);

            return (isset($a['payload']) && is_string($a['payload']) && array_diff($k, ['payload']) === [])
                || (isset($a['p']) && is_string($a['p']) && array_diff($k, ['v', 'p']) === []);
        };
        if ($all === [] || $isEnvelope($all) === false) {
            // Body may not have been parsed into $request->all() (content-type quirks, query-only input).
            $candidates = [];
            try {
                $candidates[] = $request->json()->all();
            } catch (\Throwable $e) {
                // ignore
            }
            $content = trim((string) $request->getContent());
            if ($content !== '') {
                $candidates[] = json_decode($content, true);
            }
            foreach ($candidates as $cand) {
                if (is_array($cand) && $cand !== [] && ($all === [] || $isEnvelope($cand))) {
                    $all = $cand;
                    $request->replace($cand);
                    break;
                }
            }
        }
        $enc = null;
        if (isset($all['p']) && is_string($all['p']) && array_diff(array_keys($all), ['v', 'p']) === []) {
            $enc = $all['p'];
        } elseif (isset($all['payload']) && is_string($all['payload']) && array_diff(array_keys($all), ['payload']) === []) {
            $enc = $all['payload'];
        }
        if ($enc === null || trim($enc) === '') {
            return $request;
        }
        $raw = base64_decode($enc, true);
        if ($raw === false) {
            return $request;
        }
        // Strip optional salt prefix (e.g. "analyzer-encoded-payload-v1:") up to the first '{' or '['
        $parsed = json_decode(substr($raw, strcspn($raw, '{[')), true);
        if (! is_array($parsed)) {
            return $request;
        }
        $request->replace($parsed);
        if ($request->isJson()) {
            $request->json()->replace($parsed);
        }
    } catch (\Throwable $e) {
        // leave request untouched
    }

    return $request;
}
```

---

### Lesson 2: Azure AI Reasoning Models (`gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-6.1-sol`) Reject `temperature != 1.0`

#### The Symptom
Calling `Integration::for('<name>')->ai()->chat($messages, ['model' => $model, 'temperature' => 0.4, 'max_tokens' => 4096])` throws:
```text
Analyzer\IntegrationSdk\Support\RemoteBridgeException [ai_error]: The AI request failed. Please try again or choose a different model.
```

#### The Root Cause
AI Analyzer's production AI stack routes models (`gpt-5.6-luna` [default], `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-6.1-sol`) to Azure OpenAI reasoning deployments (`o1`/`o3`/`gpt-5` family). These deployments **reject any `temperature` parameter other than `1.0` (or reject `temperature` altogether)** with an upstream HTTP 400 error, which Analyzer wraps into `RemoteBridgeException [ai_error]`.

Notice how the kit's reference integration (`paper-writer`) calls `$ai->chat()`: it passes **only** `['model' => $model, 'max_tokens' => $maxTokens]` and never passes `temperature`.

#### The Complete Fix: Copy-Pasteable `AiGateway.php` Template

Never pass `temperature` to `$ai->chat()`, and always include a retry fallback with minimal options `['model' => $resolved]`:

```php
<?php

namespace Integrations\MyApp\Services;

use Analyzer\IntegrationSdk\Integration;

/**
 * Fault-tolerant wrapper around the Analyzer AI client.
 */
final class AiGateway
{
    public const INTEGRATION = 'my-app';

    /**
     * @param  array<int, array{role: string, content: string}>  $messages
     * @return array{content: string, model: string}
     */
    public function chat(array $messages, ?string $model = null, ?int $maxTokens = null): array
    {
        $ctx = Integration::for(self::INTEGRATION);
        $ai = $ctx->ai();
        $settings = $ctx->settings();

        $resolved = trim((string) $model);
        if ($resolved === '') {
            $resolved = trim((string) ($settings['default_model'] ?? ''));
        }
        if ($resolved === '') {
            $resolved = $ai->defaultModel();
        }
        $max = $maxTokens ?? max(512, (int) ($settings['max_tokens'] ?? 8192));

        // IMPORTANT: Azure AI reasoning models (gpt-5.6-luna, gpt-5.6-sol, gpt-5.6-terra, gpt-6.1-sol)
        // reject temperature != 1.0 with RemoteBridgeException [ai_error]. Never pass temperature.
        try {
            $reply = $ai->chat($messages, [
                'model' => $resolved,
                'max_tokens' => $max,
            ]);
        } catch (\Throwable $e) {
            // Retry once with minimal options in case max_tokens is restricted on a specific deployment.
            $reply = $ai->chat($messages, ['model' => $resolved]);
        }

        return [
            'content' => (string) ($reply['content'] ?? ''),
            'model' => (string) ($reply['model'] ?? $resolved),
        ];
    }

    /** @return array<string, mixed> */
    public function settings(): array
    {
        try {
            return Integration::for(self::INTEGRATION)->settings();
        } catch (\Throwable $e) {
            return [];
        }
    }

    /** @return array{models: array<int, mixed>, defaultModel: string} */
    public function models(): array
    {
        $ai = Integration::for(self::INTEGRATION)->ai();
        try {
            $models = $ai->models();
        } catch (\Throwable $e) {
            $models = [];
        }
        $default = trim((string) ($this->settings()['default_model'] ?? ''));
        if ($default === '') {
            try {
                $default = $ai->defaultModel();
            } catch (\Throwable $e) {
                $default = '';
            }
        }

        return ['models' => $models, 'defaultModel' => $default];
    }

    public static function isFake(string $content, string $model = ''): bool
    {
        return str_starts_with(ltrim($content), '[fake]') || $model === 'fake-model';
    }
}
```

---

### Lesson 3: HTTP 200 Error Envelopes (`EnsureIntegrationBooted`) & UI State Transitions

#### The Symptom
When clicking an action button (e.g., "Plan Presentation"), the UI crashes with `TypeError: Cannot read properties of undefined (reading 'slides')` or transitions to Step 2 ("Blueprint") showing a completely blank screen with no error alert.

#### The Root Cause
Look at `Analyzer\IntegrationSdk\Http\Middleware\EnsureIntegrationBooted`:
```php
if ($this->wantsJsonResponse($request)) {
    return new JsonResponse([
        'success' => false,
        'message' => "Integration '{$name}' failed to load: {$detail}",
        'detail' => $detail,
    ], 200); // <-- HTTP 200 OK!
}
```
When an integration fails to boot (or if a controller returns `['success' => false, 'message' => '...']`), the server responds with **HTTP 200 OK**. Because `res.ok` is `true`, raw `createApi(ctx)` resolves the promise with `{ success: false, message: "...", detail: "..." }`.

If your frontend code writes:
```javascript
// BUGGY CODE — DO NOT DO THIS:
const res = await api.post('/strategist/plan', form);
setProject(res.project); // res.project is undefined!
setStep(2);              // Transitions away from Step 1 where the error banner lived!
```
Then:
1. `res.project` is `undefined`, so any component reading `project.slides.map(...)` throws `Cannot read properties of undefined (reading 'slides')`.
2. Even if guarded by `{step === 2 && project && <Blueprint project={project} />}`, setting `step = 2` when `project` is `null` hides Step 1 and renders **nothing** on Step 2!
3. If your `<Alert error={error} />` component is rendered *inside* the Step 1 component instead of at the top level of `<App />`, switching tabs or steps hides the error banner completely.

#### How to Prevent It
1. **Always use `safeApi(rawApi, ctx)`** (from Lesson 1): `checkResponse()` automatically inspects `if (r.success === false)` and throws a real `Error(r.message + ' — ' + r.detail)` even on HTTP 200 responses.
2. **Normalize all server responses** (`normalizeProject(raw)`) before setting state, and never transition `step` unless a valid object was produced.
3. **Guard step rendering at the top level**:
   ```javascript
   // Never render a step > 1 if project is null:
   const shownStep = !project && step > 1 ? 1 : step;
   ```
4. **Render `<Alert />` banners at the top level of `<App />`** (above step/tab conditionals) so configuration warnings, fallback notices, and errors remain visible across all steps.

---

### Lesson 4: Fault-Tolerant Storage (`ProjectStore`) & UTF-8 Sanitization

#### The Symptom
Saving or seeding a project throws an unhandled exception or returns HTTP 500 when:
- The host storage directory (`storage/app/integrations/<name>`) has restrictive permissions or is temporarily unmounted, or
- AI output or user source material contains invalid UTF-8 byte sequences, causing `json_encode()` to return `false`.

#### How to Prevent It: Copy-Pasteable `ProjectStore.php` Pattern
Always wrap `Integration::for('<name>')->storage()` in `try/catch (\Throwable)` with a static in-memory fallback array, sanitize IDs with `preg_replace('/[^A-Za-z0-9_\-]/', '', $id)`, and encode JSON with `JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE | JSON_PARTIAL_OUTPUT_ON_ERROR`:

```php
<?php

namespace Integrations\MyApp\Services;

use Analyzer\IntegrationSdk\Integration;

final class ProjectStore
{
    public const INTEGRATION = 'my-app';
    private const INDEX = 'projects/index.json';

    /** @var array<string, array<string, mixed>> In-memory fallback if storage disk is unavailable */
    private static array $memory = [];

    /** @var list<array<string, mixed>>|null */
    private static ?array $memoryIndex = null;

    /** @return \Illuminate\Contracts\Filesystem\Filesystem|null */
    private function disk()
    {
        try {
            return Integration::for(self::INTEGRATION)->storage();
        } catch (\Throwable $e) {
            return null;
        }
    }

    private static function encode(mixed $data): string
    {
        $json = json_encode(
            $data,
            JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE | JSON_PARTIAL_OUTPUT_ON_ERROR
        );

        return $json === false ? '{}' : $json;
    }

    private function read(string $path): ?string
    {
        try {
            $disk = $this->disk();
            if ($disk === null || ! $disk->exists($path)) {
                return null;
            }
            $raw = $disk->get($path);

            return is_string($raw) ? $raw : null;
        } catch (\Throwable $e) {
            return null;
        }
    }

    private function write(string $path, string $contents): bool
    {
        try {
            $disk = $this->disk();
            if ($disk === null) {
                return false;
            }

            return $disk->put($path, $contents) !== false;
        } catch (\Throwable $e) {
            return false;
        }
    }

    public static function newId(): string
    {
        return 'p'.date('ymdHis').bin2hex(random_bytes(3));
    }

    private static function safeId(string $id): string
    {
        return preg_replace('/[^A-Za-z0-9_\-]/', '', $id) ?? '';
    }

    /** @param array<string, mixed> $item */
    public function save(array $item): array
    {
        $id = self::safeId((string) ($item['id'] ?? ''));
        if ($id === '') {
            $id = self::newId();
        }
        $item['id'] = $id;
        $item['updated_at'] = gmdate('c');

        // Always update in-memory fallback first so read-only storage never breaks user workflows
        self::$memory[$id] = $item;
        try {
            $this->write('projects/'.$id.'.json', self::encode($item));
        } catch (\Throwable $e) {
            // keep in-memory copy
        }

        return $item;
    }

    public function get(string $id): ?array
    {
        $id = self::safeId($id);
        if ($id === '') {
            return null;
        }
        $raw = $this->read('projects/'.$id.'.json');
        $data = $raw !== null ? json_decode($raw, true) : null;
        if (is_array($data)) {
            return $data;
        }

        return self::$memory[$id] ?? null;
    }
}
```

---

## 6. Copy-Paste Starter Templates

### 6.1 `package.json`

Bundle React and any client-side file generators (`pptxgenjs`, `jszip`, etc.) directly into `dependencies`. Do **not** add `@analyzer/integration-sdk` to `package.json`—it is resolved via Vite aliases from `SDK_JS_PATH`.

```json
{
  "name": "my-app-integration",
  "private": true,
  "type": "module",
  "scripts": {
    "build": "vite build",
    "dev": "vite"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.4",
    "vite": "^6.0.3"
  }
}
```

### 6.2 `vite.config.js`

This exact `vite.config.js` works both inside the kit (`../../sdk/js`) and from any external repository when `SDK_JS_PATH` is set:

```javascript
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const integrationDir = path.dirname(fileURLToPath(import.meta.url));
const sdkJs = path.resolve(
  process.env.SDK_JS_PATH || path.join(integrationDir, '../../sdk/js')
);
const { integrationViteConfig } = await import(
  pathToFileURL(path.join(sdkJs, 'vite-preset.js')).href
);

export default defineConfig(
  integrationViteConfig({ entry: 'src/main.jsx', plugins: [react()] })
);
```

### 6.3 `src/main.jsx`

```jsx
import '@analyzer/integration-sdk/ui.css';
import './styles.css';
import { createApi, defineIntegration, mountRoot } from '@analyzer/integration-sdk';
import { createRoot } from 'react-dom/client';
import { App } from './App.jsx';

export default defineIntegration({
  mount(el, ctx) {
    const root = mountRoot(el, ctx);
    const api = createApi(ctx);
    const reactRoot = createRoot(root);
    reactRoot.render(<App api={api} ctx={ctx} />);
    return () => reactRoot.unmount();
  },
});
```

### 6.4 `src/IntegrationProvider.php`

Replace `MyApp` with `Str::studly($manifestName)` (e.g., `ppt-master` → `PptMaster`):

```php
<?php

namespace Integrations\MyApp;

use Analyzer\IntegrationSdk\IntegrationProvider as BaseIntegrationProvider;

class IntegrationProvider extends BaseIntegrationProvider
{
    public function register(): void
    {
        //
    }

    public function boot(): void
    {
        //
    }
}
```

### 6.5 `routes/api.php`

Remember: **only `GET` and `POST`** routes are allowed.

```php
<?php

use Illuminate\Support\Facades\Route;
use Integrations\MyApp\Http\Controllers\MyAppController;

Route::get('/config', [MyAppController::class, 'config']);
Route::get('/projects', [MyAppController::class, 'projects']);
Route::get('/projects/{id}', [MyAppController::class, 'project']);

Route::post('/projects/save', [MyAppController::class, 'saveProject']);
Route::post('/projects/delete', [MyAppController::class, 'deleteProject']);
Route::post('/generate', [MyAppController::class, 'generate']);
```

### 6.6 UI Scoping Rules (`docs/UI_GUIDELINES.md` Summary)

Every integration embeds directly inside the AI Analyzer shell alongside the host sidebar. Follow these mandatory CSS/UI rules:
1. **Always import `@analyzer/integration-sdk/ui.css`** and mount inside `mountRoot(el, ctx)`, which creates `<div class="aik-root" data-theme="light|dark">`.
2. **Use `aik-*` classes and `--aik-*` CSS variables**:
   - Layout: `aik-page`, `aik-page__title`, `aik-page__subtitle`, `aik-row`, `aik-grid`, `aik-card`
   - Controls: `aik-btn`, `aik-btn--primary` (max 1 per view), `aik-btn--secondary`, `aik-btn--ghost`, `aik-btn--danger` (behind `aik-modal` confirmation), `aik-input`, `aik-select`, `aik-textarea`, `aik-field`, `aik-label`
   - Navigation & Feedback: `aik-tabs`, `aik-tab`, `aik-badge`, `aik-alert`, `aik-alert--danger`, `aik-alert--warning`, `aik-spinner`, `aik-empty`, `aik-skeleton`, `aik-code`
3. **Never style global tags**: No bare `html`, `body`, `:root`, or `*` selectors in `src/styles.css`. Scope every custom class under `.aik-root` or an app-specific prefix (e.g., `.pptm-app .my-class`).
4. **No external CDNs**: Do not load Google Fonts, Bootstrap, Tailwind CDN, or external scripts. Bundle everything via Vite.
5. **Container-relative sizing**: Never use `100vw` or full-viewport fixed overlays (the host sidebar occupies part of the viewport). Keep `z-index <= 1000`.

---

## 7. Fast Build, Validation & Canonical Zip Packaging Workflow (No Full Docker Harness Required)

You do **not** need to boot the full Docker Compose dev harness (`make setup` / `:8095`) just to build, validate, and package a production-ready integration `.zip`. Using local `npm`, a lightweight `php:8.3-cli-alpine` container, and Python's `zipfile`, you can run the **exact** SDK `Packager` and `PackageValidator` in **under 30 seconds**.

### Step 1: Unpack `AI Analyzer Kit 1.0.0.zip` and Build the Vite Frontend

```bash
# 1. Unpack the kit to /tmp/aianalyzerkit (if not already unpacked)
rm -rf /tmp/aianalyzerkit
unzip -q "/Users/yaqub.mahmoud/Library/CloudStorage/Box-Box/CPT/AI/AI Analyzer Kit 1.0.0.zip" -d /tmp/aianalyzerkit

# 2. Install frontend dependencies and build dist/ using the kit's JS SDK
INTEGRATION_DIR="/path/to/your/integrations/ppt-master"
npm --prefix "$INTEGRATION_DIR" install
SDK_JS_PATH="/tmp/aianalyzerkit/aianalyzerkit/sdk/js" npm --prefix "$INTEGRATION_DIR" run build
```

Verify that `$INTEGRATION_DIR/dist/.vite/manifest.json` exists and contains your `"src/main.jsx"` entry.

### Step 2: Run `php -l`, `Packager::stage()`, and `PackageValidator::validateDirectory()` in PHP 8.3

This single `docker run` command stages only whitelisted files into `/tmp/integration-stage/pkg` (stripping `node_modules/`, `src/*.jsx`, `package.json`, etc.) and runs the official `PackageValidator` (including `php -l` syntax checks and token scanning) on every file:

```bash
rm -rf /tmp/integration-stage && mkdir -p /tmp/integration-stage

docker run --rm \
  -v "/tmp/aianalyzerkit/aianalyzerkit/sdk:/sdk:ro" \
  -v "$INTEGRATION_DIR:/src:ro" \
  -v "/tmp/integration-stage:/stage" \
  php:8.3-cli-alpine php -r '
namespace Illuminate\Support {
    class Str {
        public static function studly($v) {
            return str_replace(" ", "", ucwords(str_replace(["-", "_"], " ", $v)));
        }
    }
}
namespace {
    function config($k = null, $d = null) { return $d; }
    function storage_path($p = "") { return "/tmp/" . $p; }

    $sdk = "/sdk/src/";
    foreach ([
        "Sdk",
        "ViteManifestReader",
        "Support/IntegrationPaths",
        "ManifestException",
        "Manifest",
        "ValidationResult",
        "PackageReport",
        "SafeZip",
        "PackageValidator",
        "Packager"
    ] as $f) {
        require $sdk . $f . ".php";
    }

    $validator = new Analyzer\IntegrationSdk\PackageValidator();
    $packager  = new Analyzer\IntegrationSdk\Packager($validator);

    $target = "/stage/pkg";
    $packager->stage("/src", $target);

    $result = $validator->validateDirectory($target);
    echo "ok=" . var_export($result->ok(), true) . "\n";
    echo "errors=" . json_encode($result->errors, JSON_UNESCAPED_SLASHES) . "\n";
    echo "warnings=" . json_encode($result->warnings, JSON_UNESCAPED_SLASHES) . "\n";

    $manifest = Analyzer\IntegrationSdk\Manifest::fromFile($target . "/integration.json");
    echo "namespace=" . $manifest->namespace()
       . " provider=" . $manifest->providerClass()
       . " version=" . $manifest->version()
       . " sdkOk=" . var_export(Analyzer\IntegrationSdk\Sdk::satisfies("1.0.0", $manifest->sdkConstraint()), true) . "\n";

    if (!$result->ok() || !empty($result->warnings)) {
        exit(1);
    }
}
'
```

**Expected Output**:
```text
ok=true
errors=[]
warnings=[]
namespace=Integrations\PptMaster\ provider=Integrations\PptMaster\IntegrationProvider version=1.0.3 sdkOk=true
```

### Step 3: Package the Staged Directory into a Canonical File-Only `.zip` (0 Directory Entries)

Because `php:8.3-cli-alpine` does not bundle the `ext-zip` C extension by default, create the final `.zip` from `/tmp/integration-stage/pkg` using Python's standard `zipfile` module (which produces the exact file-only archive structure of `Packager::createZipFromDirectory()`):

```bash
OUTPUT_ZIP="/Users/yaqub.mahmoud/github/ppt-master/ppt-master-1.0.3.zip"

python3 -c '
import os, sys, zipfile

stage_dir, out_zip = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(stage_dir):
        dirs.sort()
        for f in sorted(files):
            full_path = os.path.join(root, f)
            if os.path.isfile(full_path) and not os.path.islink(full_path):
                rel_path = os.path.relpath(full_path, stage_dir).replace(os.sep, "/")
                zf.write(full_path, rel_path)
print("Created:", out_zip)
' "/tmp/integration-stage/pkg" "$OUTPUT_ZIP"

# Verify archive structure with zipinfo (must have 0 directory entries and integration.json at root)
zipinfo "$OUTPUT_ZIP"
```

**Checklist for `zipinfo` Output**:
- [ ] Every line starts with `-rw-r--r--` (zero `drwxr-xr-x` directory entries).
- [ ] `integration.json` is at the top level (not inside a subfolder).
- [ ] `dist/.vite/manifest.json` and `dist/assets/*` are present.
- [ ] `src/` contains **only** `.php` files.
- [ ] No `.env`, `.DS_Store`, `node_modules/`, or `vendor/` entries exist.

### Optional Step 4: Live Remote Bridge Diagnostic Against AI Analyzer

The `AI Analyzer Kit 1.0.0.zip` includes a `.env` file with `ANALYZER_URL` and a scoped `ANALYZER_TOKEN` (`integration-devkit` ability, valid for 90 days; rotated each time the kit zip is downloaded).

> **SECURITY RULE**: **Never** commit `.env` or `ANALYZER_TOKEN` to git, and **never** include `.env` inside an integration directory or `.zip`.

You can test live connectivity, list production models (`gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-6.1-sol`), and verify that your `AiGateway` options work against the live Analyzer host from your terminal:

```bash
python3 - <<'PY'
import base64, json, urllib.request, ssl

# Read ANALYZER_URL and ANALYZER_TOKEN from the unpacked kit's .env
env = {}
with open("/tmp/aianalyzerkit/aianalyzerkit/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")

base_url = env["ANALYZER_URL"].rstrip("/")
token = env["ANALYZER_TOKEN"]

def bridge_post(endpoint, payload):
    url = f"{base_url}/api/integration-sdk/v1/{endpoint.lstrip('/')}"
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    body = json.dumps({"p": encoded}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-payload-encoded": "1",
            "Authorization": f"Bearer {token}",
        },
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

print("1. whoami:", json.dumps(bridge_post("/whoami", {}), indent=2))
models_res = bridge_post("/ai/models", {})
print("2. models:", json.dumps(models_res, indent=2))

default_model = models_res.get("data", {}).get("default_model", "gpt-5.6-luna")
chat_res = bridge_post("/ai/chat", {
    "integration": "ppt-master",
    "messages": [{"role": "user", "content": "Reply with the single word READY."}],
    "options": {"model": default_model, "max_tokens": 512},  # Note: NO temperature parameter!
})
print("3. chat:", json.dumps(chat_res, indent=2))
PY
```

---

## Quick Troubleshooting Matrix

| Symptom on AI Analyzer | Root Cause | Immediate Fix |
| :--- | :--- | :--- |
| Uploaded `.zip`, but nothing appears in sidebar and `/integrations/<name>` returns **404** | `Installer::install()` sets `"enabled": false` in `registry.json` on first install. | Open **Integrations → Manage** (`/integrations`), toggle **Enable** on your integration, and reload. |
| Re-uploading `.zip` fails with `"upgrade version must be greater than installed"` | `Installer::upgrade()` enforces `newVersion > oldVersion` via `Sdk::compareSemver()`. | Bump `"version"` in `integration.json` (e.g., `1.0.2` → `1.0.3`), or remove the integration first before uploading. |
| `POST` requests return **HTTP 200 OK** with `<TITLE>Error occurred/エラーが発生しました</TITLE>` | `sdk/js/index.js` sends `encrypt: true` with salted `{"v":1,"p":"..."}`, which F5 WAF / `ProbePayloadGuard` blocks. | Wrap `createApi(ctx)` with `safeApi(rawApi, ctx)` to send unsalted `{"payload": base64(json)}` + retry without `encrypt` header, and add `decoded(Request $request)` in your PHP controller (Lesson 1). |
| AI generation fails with `RemoteBridgeException [ai_error]: The AI request failed` | Passing `temperature != 1.0` (e.g., `0.4` or `0.7`) to Azure reasoning models (`gpt-5.6-*`, `gpt-6.1-*`). | Omit `temperature` from `$ai->chat($messages, ['model' => $resolved, 'max_tokens' => $max])` (Lesson 2). |
| Clicking action button crashes with `Cannot read properties of undefined` or shows blank tab | `EnsureIntegrationBooted` or controller returned `{success: false, message: "..."}` with **HTTP 200**, and UI advanced `step` without a valid object. | Unwrap responses via `checkResponse()` in `safeApi`, normalize objects before state updates, and guard `const shownStep = !project && step > 1 ? 1 : step` (Lesson 3). |
| `PackageValidator` fails with `reference to host namespace App\` even though no `use App\...` exists | A PHP comment or docblock mentioned `App\...` or `Libs\...`, matching `/\b(App\|Libs)\\[A-Za-z0-9_\\]+/`. | Remove all occurrences of `App\` and `Libs\` from comments, strings, and code in `.php` files. |
| Static prompt/template files under `src/` are missing at runtime (`file_get_contents` fails) | `Packager::shippableFiles()` strips all non-`.php` files from `src/`. | Move all `.md`, `.json`, and `.txt` files into `resources/` and load them via `Integration::for('<name>')->path('resources/...')`. |
