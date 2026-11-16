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
    private function remote(Request $request): RemoteBackendBridge
    {
        $url = (string) ($request->input('remote_url') ?? '');
        if (trim($url) === '') {
            $url = (string) ((new AiGateway)->settings()['ppt_master_backend_url'] ?? '');
        }

        return new RemoteBackendBridge($url);
    }

    private function fail(string $message, int $status = 422): JsonResponse
    {
        return response()->json(['message' => $message], $status);
    }

    public function config(): JsonResponse
    {
        $ai = new AiGateway;
        $prompts = new PromptRepository;
        $settings = $ai->settings();
        $m = $ai->models();
        $remote = new RemoteBackendBridge((string) ($settings['ppt_master_backend_url'] ?? ''));
        $status = $remote->configured() ? $remote->status() : ['configured' => false, 'url' => '', 'reachable' => false];

        return response()->json([
            'models' => $m['models'],
            'defaultModel' => $m['defaultModel'],
            'brands' => $prompts->brands(),
            'canvasFormats' => $prompts->canvasFormats(),
            'settings' => $settings,
            'remoteBackend' => $status,
        ]);
    }

    public function projects(): JsonResponse
    {
        return response()->json((new ProjectStore)->listAll());
    }

    public function project(string $id): JsonResponse
    {
        $p = (new ProjectStore)->get($id);

        return $p === null ? $this->fail('Project not found.', 404) : response()->json(['project' => $p]);
    }

    public function saveProject(Request $request): JsonResponse
    {
        $project = $request->input('project');
        if (! is_array($project)) {
            return $this->fail('project is required.');
        }
        unset($project['is_example']);

        return response()->json(['project' => (new ProjectStore)->save($project)]);
    }

    public function deleteProject(Request $request): JsonResponse
    {
        $id = (string) $request->input('id', '');
        if ($id === '') {
            return $this->fail('id is required.');
        }
        (new ProjectStore)->delete($id);

        return response()->json(['ok' => true]);
    }

    public function plan(Request $request): JsonResponse
    {
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
            return response()->json(['message' => 'Please enter a topic.', 'errors' => $v->errors()], 422);
        }
        try {
            $result = (new StrategistService)->plan($request->all());
        } catch (\Throwable $e) {
            return $this->fail('Planning failed: '.$e->getMessage(), 500);
        }
        $plan = $result['plan'];
        $project = (new ProjectStore)->save([
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
        ]);

        return response()->json(['project' => $project, 'model' => $result['model'], 'source' => $result['source']]);
    }

    public function confirm(Request $request): JsonResponse
    {
        $store = new ProjectStore;
        $project = $store->get((string) $request->input('id', ''));
        if ($project === null) {
            return $this->fail('Project not found.', 404);
        }
        foreach (['eight_confirmations', 'design_spec', 'slides'] as $key) {
            if (is_array($request->input($key))) {
                $project[$key] = $request->input($key);
            }
        }
        $slides = [];
        foreach (array_values((array) $project['slides']) as $i => $s) {
            if (is_array($s)) {
                $s['slide_number'] = $i + 1;
                $slides[] = $s;
            }
        }
        $project['slides'] = $slides;
        $project['slide_count'] = count($slides);
        $project['svgs'] = [];
        $project['status'] = 'confirmed';

        return response()->json(['project' => $store->save($project)]);
    }

    public function slide(Request $request): JsonResponse
    {
        $store = new ProjectStore;
        $projectId = (string) $request->input('project_id', '');
        $project = $projectId !== '' ? $store->get($projectId) : null;
        $input = $request->all();
        if ($project !== null) {
            $input['topic'] = $input['topic'] ?? $project['topic'];
            $input['total_slides'] = count((array) $project['slides']);
            foreach (['eight_confirmations', 'design_spec', 'brand', 'canvas_format'] as $k) {
                if (empty($input[$k]) && isset($project[$k])) {
                    $input[$k] = $project[$k];
                }
            }
        }
        if (! is_array($input['slide_plan'] ?? null)) {
            return $this->fail('slide_plan is required.');
        }
        try {
            $result = (new ExecutorService)->slide($input);
        } catch (\Throwable $e) {
            return $this->fail('Slide generation failed: '.$e->getMessage(), 500);
        }
        if ($project !== null) {
            $slidePlan = $input['slide_plan'];
            $store->setSvg($projectId, $result['slide_number'], $result['svg'], is_array($slidePlan) ? $slidePlan : null);
        }

        return response()->json([
            'slide_number' => $result['slide_number'],
            'svg' => $result['svg'],
            'slide' => $result['slide'],
            'model' => $result['model'],
            'warnings' => $result['warnings'],
            'fallback' => $result['fallback'],
        ]);
    }

    public function finalizeSvg(Request $request): JsonResponse
    {
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
            $store->setSvg($projectId, $n, $clean);
        }

        return response()->json(['svg' => $clean, 'valid' => true, 'warnings' => $warnings]);
    }

    // ----------------------------------------------------------------- remote bridge

    public function remoteStatus(Request $request): JsonResponse
    {
        return response()->json($this->remote($request)->status());
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
        $conf = $request->input('confirmations');

        return $this->proxy(function () use ($request, $id, $conf) {
            $this->remote($request)->confirmJob($id, is_array($conf) ? $conf : []);

            return ['ok' => true];
        });
    }

    public function remoteDownload(Request $request, string $id): JsonResponse
    {
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
