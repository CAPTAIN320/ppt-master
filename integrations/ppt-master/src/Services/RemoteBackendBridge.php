<?php

namespace Integrations\PptMaster\Services;

use Illuminate\Support\Facades\Http;

/**
 * Optional proxy to an external Python PPT Master FastAPI server (web/server.py).
 */
final class RemoteBackendBridge
{
    private string $base;

    public function __construct(?string $url = null)
    {
        $this->base = rtrim(trim((string) $url), '/');
    }

    public static function normalize(?string $url): string
    {
        $url = rtrim(trim((string) $url), '/');
        if ($url === '' || preg_match('#^https?://#i', $url) !== 1) {
            return '';
        }

        return $url;
    }

    public function configured(): bool
    {
        return self::normalize($this->base) !== '';
    }

    /** @return array{configured: bool, url: string, reachable: bool} */
    public function status(int $timeout = 2): array
    {
        $url = self::normalize($this->base);
        if ($url === '') {
            return ['configured' => false, 'url' => '', 'reachable' => false];
        }
        $ok = false;
        try {
            $ok = Http::timeout(max(1, $timeout))->acceptJson()->get($url.'/jobs')->successful();
        } catch (\Throwable $e) {
            $ok = false;
        }

        return ['configured' => true, 'url' => $url, 'reachable' => $ok];
    }

    private function url(string $path): string
    {
        $base = self::normalize($this->base);
        if ($base === '') {
            throw new \RuntimeException('Remote PPT Master backend URL is not configured.');
        }

        return $base.$path;
    }

    /** @return array<string, mixed> */
    private function json(\Illuminate\Http\Client\Response $res): array
    {
        if (! $res->successful()) {
            throw new \RuntimeException('Remote backend returned HTTP '.$res->status().': '.mb_substr((string) $res->body(), 0, 300));
        }
        $data = $res->json();

        return is_array($data) ? $data : [];
    }

    /** @return array<string, mixed> */
    public function listJobs(): array
    {
        $data = $this->json(Http::timeout(30)->acceptJson()->get($this->url('/jobs')));

        return isset($data['jobs']) ? $data : ['jobs' => array_is_list($data) ? $data : []];
    }

    /**
     * @param  array<string, mixed>  $payload
     * @return array<string, mixed>
     */
    public function createJob(array $payload): array
    {
        $fields = [];
        foreach (['topic', 'canvas_format', 'model', 'theme', 'slide_count_pref', 'image_usage_pref'] as $k) {
            if (isset($payload[$k]) && $payload[$k] !== '') {
                $fields[$k] = (string) $payload[$k];
            }
        }
        $fields['auto_confirm'] = ! empty($payload['auto_confirm']) && $payload['auto_confirm'] !== 'false' ? 'true' : 'false';
        $req = Http::timeout(60)->acceptJson()->asMultipart();
        $source = (string) ($payload['source_material'] ?? '');
        if ($source !== '') {
            $req = $req->attach('files', $source, 'source.md');
        }
        $multipart = [];
        foreach ($fields as $k => $v) {
            $multipart[] = ['name' => $k, 'contents' => $v];
        }

        return $this->json($req->post($this->url('/jobs'), $multipart));
    }

    /** @return array<string, mixed> */
    public function getJob(string $jobId): array
    {
        $job = $this->json(Http::timeout(30)->acceptJson()->get($this->url('/jobs/'.rawurlencode($jobId))));

        // R1: FastAPI stores these as JSON text columns.
        foreach (['confirm_data', 'confirm_result'] as $k) {
            if (isset($job[$k]) && is_string($job[$k]) && trim($job[$k]) !== '') {
                $decoded = json_decode($job[$k], true);
                if (is_array($decoded)) {
                    $job[$k] = $decoded;
                }
            }
        }
        // R2: surface the backend error under the key the UI reads.
        if (empty($job['error']) && ! empty($job['error_message'])) {
            $job['error'] = $job['error_message'];
        }
        // R3: slide_count is only maintained by the websocket; recompute from the jobs list or by probing.
        if ((int) ($job['slide_count'] ?? 0) <= 0 && ($job['status'] ?? '') === 'done') {
            $job['slide_count'] = $this->probeSlideCount($jobId);
        }

        return $job;
    }

    private function probeSlideCount(string $jobId): int
    {
        try {
            $list = $this->listJobs()['jobs'] ?? [];
            foreach ($list as $j) {
                if (is_array($j) && (string) ($j['id'] ?? '') === $jobId && (int) ($j['slide_count'] ?? 0) > 0) {
                    return (int) $j['slide_count'];
                }
            }
        } catch (\Throwable $e) {
            // fall through to probing
        }
        $count = 0;
        for ($n = 1; $n <= 60; $n++) {
            try {
                $res = Http::timeout(10)->get($this->url('/jobs/'.rawurlencode($jobId).'/slides/'.$n));
            } catch (\Throwable $e) {
                break;
            }
            if (! $res->successful()) {
                break;
            }
            $count = $n;
        }

        return $count;
    }

    /**
     * @param  array<string, mixed>  $confirmations
     * @return array<string, mixed>
     */
    public function confirmJob(string $jobId, array $confirmations): array
    {
        return $this->json(Http::timeout(60)->acceptJson()->asJson()->post($this->url('/jobs/'.rawurlencode($jobId).'/confirm'), $confirmations));
    }

    public function getSlideSvg(string $jobId, int $slideIndex): string
    {
        $res = Http::timeout(60)->get($this->url('/jobs/'.rawurlencode($jobId).'/slides/'.$slideIndex));
        if (! $res->successful()) {
            throw new \RuntimeException('Remote backend returned HTTP '.$res->status());
        }

        return (string) $res->body();
    }

    /** @return array{filename: string, mimeType: string, base64: string} */
    public function downloadPptx(string $jobId, string $animation = 'none'): array
    {
        $animation = $animation === 'auto' ? 'auto' : 'none';
        $res = Http::timeout(300)->get($this->url('/jobs/'.rawurlencode($jobId).'/download'), ['animation' => $animation]);
        if (! $res->successful()) {
            throw new \RuntimeException('Remote backend returned HTTP '.$res->status());
        }
        $safe = preg_replace('/[^A-Za-z0-9_\-]/', '', $jobId) ?: 'deck';

        return [
            'filename' => 'ppt-master-'.$safe.'.pptx',
            'mimeType' => 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'base64' => base64_encode((string) $res->body()),
        ];
    }
}
