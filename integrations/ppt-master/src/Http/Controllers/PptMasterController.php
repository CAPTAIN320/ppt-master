<?php

namespace Integrations\PptMaster\Http\Controllers;

use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Validator;
use Integrations\PptMaster\Services\AiGateway;
use Integrations\PptMaster\Services\ExecutorService;
use Integrations\PptMaster\Services\ProjectStore;
use Integrations\PptMaster\Services\RemoteBackendBridge;
use Integrations\PptMaster\Services\StrategistService;
use Integrations\PptMaster\Support\PromptRepository;
use Integrations\PptMaster\Support\SvgFinalizer;

class PptMasterController
{
    /**
     * Decode {v:1,p:base64(salt+json)} or {payload:base64(json)} when the host middleware did not.
     * No-op when the request is already decoded.
     */
    private function decoded(Request $request): Request
    {
        try {
            $all = $request->all();
            if ($all === [] && trim((string) $request->getContent()) !== '') {
                $raw = json_decode((string) $request->getContent(), true);
                if (is_array($raw)) {
                    $all = $raw;
                    $request->replace($raw);
                }
            }
            $keys = array_keys($all);
            $enc = null;
            if (isset($all['p']) && is_string($all['p']) && array_key_exists('v', $all) && array_diff($keys, ['v', 'p']) === []) {
                $enc = $all['p'];
            } elseif (isset($all['payload']) && is_string($all['payload']) && array_diff($keys, ['payload']) === []) {
                $enc = $all['payload'];
            }
            if ($enc === null || trim($enc) === '') {
                return $request;
            }
            $raw = base64_decode($enc, true);
            if ($raw === false) {
                return $request;
            }
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

    private function isLocalUrl(string $url): bool
    {
        $host = strtolower((string) parse_url($url, PHP_URL_HOST));

        return in_array($host, ['localhost', '127.0.0.1', '::1', '[::1]', 'host.docker.internal'], true);
    }

    private function configuredBackendUrl(): string
    {
        try {
            return (string) ((new AiGateway)->settings()['ppt_master_backend_url'] ?? '');
        } catch (\Throwable $e) {
            return '';
        }
    }

    private function remote(Request $request): RemoteBackendBridge
    {
        $configured = RemoteBackendBridge::normalize($this->configuredBackendUrl());
        $requested = RemoteBackendBridge::normalize((string) ($request->input('remote_url') ?? $request->query('remote_url') ?? ''));
        $url = $configured;
        // Only the admin-configured URL or a local/dev backend is accepted (SSRF protection).
        if ($requested !== '' && ($requested === $configured || $this->isLocalUrl($requested))) {
            $url = $requested;
        }

        return new RemoteBackendBridge($url);
    }

    private function fail(string $message, int $status = 422): JsonResponse
    {
        return response()->json(['success' => false, 'message' => $message], $status);
    }

    private function crash(string $what, \Throwable $e, int $status = 500): JsonResponse
    {
        return $this->fail($what.': '.($e->getMessage() !== '' ? $e->getMessage() : get_class($e)), $status);
    }

    public function config(): JsonResponse
    {
        try {
            $ai = new AiGateway;
            $prompts = new PromptRepository;
            try {
                $settings = $ai->settings();
            } catch (\Throwable $e) {
                $settings = [];
            }
            try {
                $m = $ai->models();
            } catch (\Throwable $e) {
                $m = ['models' => [], 'defaultModel' => ''];
            }
            $status = ['configured' => false, 'url' => '', 'reachable' => false];
            $backend = RemoteBackendBridge::normalize((string) ($settings['ppt_master_backend_url'] ?? ''));
            if ($backend !== '') {
                // Fast probe only; never block first paint on a dead backend.
                $status = (new RemoteBackendBridge($backend))->status(2);
            }

            return response()->json([
                'models' => $m['models'] ?? [],
                'defaultModel' => $m['defaultModel'] ?? '',
                'brands' => $prompts->brands(),
                'canvasFormats' => $prompts->canvasFormats(),
                'settings' => $settings,
                'remoteBackend' => $status,
            ]);
        } catch (\Throwable $e) {
            return $this->crash('Could not load configuration', $e);
        }
    }

    public function projects(): JsonResponse
    {
        try {
            return response()->json((new ProjectStore)->listAll());
        } catch (\Throwable $e) {
            return response()->json(['projects' => [], 'examples' => []]);
        }
    }

    public function project(string $id): JsonResponse
    {
        try {
            $p = (new ProjectStore)->get($id);

            return $p === null ? $this->fail('Project not found.', 404) : response()->json(['project' => $p, 'slides' => $p['slides'] ?? [], 'id' => $p['id'] ?? $id]);
        } catch (\Throwable $e) {
            return $this->crash('Could not load project', $e);
        }
    }

    public function saveProject(Request $request): JsonResponse
    {
        try {
            $request = $this->decoded($request);
            $project = $request->input('project');
            if (! is_array($project)) {
                return $this->fail('project is required.');
            }
            unset($project['is_example']);
            $saved = (new ProjectStore)->save($project);

            return response()->json($this->envelope($saved));
        } catch (\Throwable $e) {
            return $this->crash('Could not save project', $e);
        }
    }

    public function deleteProject(Request $request): JsonResponse
    {
        try {
            $request = $this->decoded($request);
            $id = (string) $request->input('id', '');
            if ($id === '') {
                return $this->fail('id is required.');
            }
            (new ProjectStore)->delete($id);

            return response()->json(['ok' => true]);
        } catch (\Throwable $e) {
            return $this->crash('Could not delete project', $e);
        }
    }

    /**
     * Project envelope with duplicated top-level keys so clients tolerate different unwrap strategies.
     *
     * @param  array<string, mixed>  $project
     * @return array<string, mixed>
     */
    private function envelope(array $project, array $extra = []): array
    {
        return [
            'project' => $project,
            'id' => $project['id'] ?? null,
            'slides' => $project['slides'] ?? [],
            'eight_confirmations' => $project['eight_confirmations'] ?? [],
            'design_spec' => $project['design_spec'] ?? [],
        ] + $extra;
    }

    public function plan(Request $request): JsonResponse
    {
        try {
            $request = $this->decoded($request);
            $v = Validator::make($request->all(), [
                'topic' => 'required|string|max:2000',
                'source_material' => 'nullable|string|max:200000',
                'brand' => 'nullable|string|max:40',
                'canvas_format' => 'nullable|string|max:20',
                'slide_count' => 'nullable',
                'structure_mode' => 'nullable|string|max:30',
                'model' => 'nullable|string|max:120',
            ]);
            if ($v->fails()) {
                return response()->json(['success' => false, 'message' => 'Please enter a topic.', 'errors' => $v->errors()], 422);
            }
            $result = (new StrategistService)->plan($request->all());
            $plan = $result['plan'];
            $project = [
                'id' => ProjectStore::newId(),
                'topic' => (string) $request->input('topic'),
                'source_material' => (string) $request->input('source_material', ''),
                'brand' => (string) $request->input('brand', 'none'),
                'canvas_format' => (string) $request->input('canvas_format', 'ppt169'),
                'model' => $result['model'],
                'structure_mode' => (string) $request->input('structure_mode', 'pyramid'),
                'slide_count' => count($plan['slides']),
                'status' => 'confirm_pending',
                'eight_confirmations' => $plan['eight_confirmations'],
                'design_spec' => $plan['design_spec'],
                'slides' => $plan['slides'],
                'svgs' => [],
            ];
            try {
                $project = (new ProjectStore)->save($project);
            } catch (\Throwable $e) {
                // Storage unavailable: still return the plan so planning succeeds.
                $project['created_at'] = $project['updated_at'] = gmdate('c');
            }

            return response()->json($this->envelope($project, ['model' => $result['model'], 'source' => $result['source']]));
        } catch (\Throwable $e) {
            return $this->crash('Planning failed', $e);
        }
    }

    public function confirm(Request $request): JsonResponse
    {
        try {
            $request = $this->decoded($request);
            $store = new ProjectStore;
            $project = $store->get((string) $request->input('id', ''));
            if ($project === null) {
                // Not persisted (e.g. unavailable storage): rebuild from the posted blueprint.
                if (! is_array($request->input('slides'))) {
                    return $this->fail('Project not found.', 404);
                }
                $project = ['id' => (string) $request->input('id', ''), 'topic' => (string) $request->input('topic', ''), 'slides' => []];
            }
            foreach (['eight_confirmations', 'design_spec', 'slides'] as $key) {
                if (is_array($request->input($key))) {
                    $project[$key] = $request->input($key);
                }
            }
            $slides = [];
            foreach (array_values((array) ($project['slides'] ?? [])) as $i => $s) {
                if (is_array($s)) {
                    $s['slide_number'] = $i + 1;
                    $slides[] = $s;
                }
            }
            $project['slides'] = $slides;
            $project['slide_count'] = count($slides);
            $project['svgs'] = [];
            $project['status'] = 'confirmed';
            try {
                $project = $store->save($project);
            } catch (\Throwable $e) {
                // return the in-memory project
            }

            return response()->json($this->envelope($project));
        } catch (\Throwable $e) {
            return $this->crash('Confirmation failed', $e);
        }
    }

    public function slide(Request $request): JsonResponse
    {
        try {
            $request = $this->decoded($request);
            $store = new ProjectStore;
            $projectId = (string) $request->input('project_id', '');
            $project = $projectId !== '' ? $store->get($projectId) : null;
            $input = $request->all();
            if ($project !== null) {
                $input['topic'] = $input['topic'] ?? ($project['topic'] ?? '');
                $input['total_slides'] = count((array) ($project['slides'] ?? []));
                foreach (['eight_confirmations', 'design_spec', 'brand', 'canvas_format'] as $k) {
                    if (empty($input[$k]) && isset($project[$k])) {
                        $input[$k] = $project[$k];
                    }
                }
            }
            if (! is_array($input['slide_plan'] ?? null)) {
                return $this->fail('slide_plan is required.');
            }
            $result = (new ExecutorService)->slide($input);
            if ($project !== null) {
                try {
                    $slidePlan = $input['slide_plan'];
                    $store->setSvg($projectId, $result['slide_number'], $result['svg'], is_array($slidePlan) ? $slidePlan : null);
                } catch (\Throwable $e) {
                    // persistence is best-effort
                }
            }

            return response()->json([
                'slide_number' => $result['slide_number'],
                'svg' => $result['svg'],
                'slide' => $result['slide'],
                'model' => $result['model'],
                'warnings' => $result['warnings'],
                'fallback' => $result['fallback'],
            ]);
        } catch (\Throwable $e) {
            return $this->crash('Slide generation failed', $e);
        }
    }

    public function finalizeSvg(Request $request): JsonResponse
    {
        try {
            $request = $this->decoded($request);
            $svg = (string) $request->input('svg', '');
            if (trim($svg) === '') {
                return $this->fail('svg is required.');
            }
            $format = (string) $request->input('canvas_format', '');
            $store = new ProjectStore;
            $projectId = (string) $request->input('project_id', '');
            $project = $projectId !== '' ? $store->get($projectId) : null;
            if ($format === '' && $project !== null) {
                $format = (string) ($project['canvas_format'] ?? 'ppt169');
            }
            $warnings = [];
            $clean = SvgFinalizer::repair($svg, $format ?: 'ppt169', $warnings);
            if ($clean === null) {
                return response()->json(['svg' => $svg, 'valid' => false, 'warnings' => $warnings]);
            }
            $n = (int) $request->input('slide_number', 0);
            if ($project !== null && $n > 0) {
                try {
                    $store->setSvg($projectId, $n, $clean);
                } catch (\Throwable $e) {
                    // best-effort
                }
            }

            return response()->json(['svg' => $clean, 'valid' => true, 'warnings' => $warnings]);
        } catch (\Throwable $e) {
            return $this->crash('SVG validation failed', $e);
        }
    }

    // ----------------------------------------------------------------- remote bridge

    public function remoteStatus(Request $request): JsonResponse
    {
        return $this->proxy(fn () => $this->remote($request)->status(2));
    }

    public function remoteJobs(Request $request): JsonResponse
    {
        return $this->proxy(fn () => $this->remote($request)->listJobs());
    }

    public function remoteJob(Request $request, string $id): JsonResponse
    {
        return $this->proxy(fn () => ['job' => $this->remote($request)->getJob($id)]);
    }

    public function remoteSlide(Request $request, string $id, string $n): JsonResponse
    {
        return $this->proxy(fn () => ['svg' => $this->remote($request)->getSlideSvg($id, (int) $n)]);
    }

    public function remoteCreate(Request $request): JsonResponse
    {
        $request = $this->decoded($request);
        if (trim((string) $request->input('topic', '')) === '') {
            return $this->fail('topic is required.');
        }

        return $this->proxy(function () use ($request) {
            $res = $this->remote($request)->createJob($request->all());

            return ['job_id' => $res['job_id'] ?? ($res['id'] ?? null), 'status' => $res['status'] ?? 'queued'];
        });
    }

    public function remoteConfirm(Request $request, string $id): JsonResponse
    {
        $request = $this->decoded($request);
        $conf = $request->input('confirmations');

        return $this->proxy(function () use ($request, $id, $conf) {
            $this->remote($request)->confirmJob($id, is_array($conf) ? $conf : []);

            return ['ok' => true];
        });
    }

    public function remoteDownload(Request $request, string $id): JsonResponse
    {
        $request = $this->decoded($request);

        return $this->proxy(fn () => $this->remote($request)->downloadPptx($id, (string) $request->input('animation', 'none')));
    }

    private function proxy(callable $fn): JsonResponse
    {
        try {
            return response()->json($fn());
        } catch (\Throwable $e) {
            return $this->fail($e->getMessage(), 502);
        }
    }
}
