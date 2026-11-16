<?php

namespace Integrations\PptMaster\Services;

use Analyzer\IntegrationSdk\Integration;
use Integrations\PptMaster\Support\PromptRepository;

/**
 * JSON project persistence on the integration storage disk.
 */
final class ProjectStore
{
    private const INDEX = 'projects/index.json';

    /** @var array<string, array<string, mixed>> in-memory fallback used when the storage disk is unavailable */
    private static array $memory = [];

    /** @var list<array<string, mixed>>|null */
    private static ?array $memoryIndex = null;

    /** @return \Illuminate\Contracts\Filesystem\Filesystem|null  null when the host storage disk is unavailable */
    private function disk()
    {
        try {
            return Integration::for(PromptRepository::INTEGRATION)->storage();
        } catch (\Throwable $e) {
            return null;
        }
    }

    private static function encode(mixed $data): string
    {
        $json = json_encode($data, JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE | JSON_PARTIAL_OUTPUT_ON_ERROR);

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

    /** @return list<array<string, mixed>> */
    private function readIndex(): array
    {
        $raw = $this->read(self::INDEX);
        $data = $raw !== null ? json_decode($raw, true) : null;
        if (is_array($data)) {
            return array_values(array_filter($data, 'is_array'));
        }

        return self::$memoryIndex ?? [];
    }

    /** @param  list<array<string, mixed>>  $index */
    private function writeIndex(array $index): void
    {
        $index = array_values($index);
        self::$memoryIndex = $index;
        $this->write(self::INDEX, self::encode($index));
    }

    /** @return array<string, mixed> */
    private function summary(array $p): array
    {
        return [
            'id' => $p['id'],
            'topic' => (string) ($p['topic'] ?? ''),
            'brand' => (string) ($p['brand'] ?? 'none'),
            'canvas_format' => (string) ($p['canvas_format'] ?? 'ppt169'),
            'status' => (string) ($p['status'] ?? 'confirm_pending'),
            'slide_count' => count((array) ($p['slides'] ?? [])),
            'svg_count' => count((array) ($p['svgs'] ?? [])),
            'model' => (string) ($p['model'] ?? ''),
            'updated_at' => (string) ($p['updated_at'] ?? ''),
            'is_example' => ! empty($p['is_example']),
        ];
    }

    /** @return array{projects: list<array<string, mixed>>, examples: list<array<string, mixed>>} */
    public function listAll(): array
    {
        $this->seedIfEmpty();
        $projects = [];
        $examples = [];
        foreach ($this->readIndex() as $s) {
            if (! empty($s['is_example'])) {
                $examples[] = $s;
            } else {
                $projects[] = $s;
            }
        }
        usort($projects, static fn ($a, $b) => strcmp((string) ($b['updated_at'] ?? ''), (string) ($a['updated_at'] ?? '')));

        return ['projects' => $projects, 'examples' => $examples];
    }

    /** @return array<string, mixed>|null */
    public function get(string $id): ?array
    {
        $id = self::safeId($id);
        if ($id === '') {
            return null;
        }
        $this->seedIfEmpty();
        $path = 'projects/'.$id.'.json';
        $raw = $this->read($path);
        $data = $raw !== null ? json_decode($raw, true) : null;
        if (is_array($data)) {
            return $data;
        }

        return self::$memory[$id] ?? null;
    }

    /**
     * @param  array<string, mixed>  $project
     * @return array<string, mixed>
     */
    public function save(array $project): array
    {
        $id = self::safeId((string) ($project['id'] ?? ''));
        $existing = $id !== '' ? $this->get($id) : null;
        if ($id === '') {
            $id = self::newId();
        }
        $project = array_replace($existing ?? [], $project);
        $project['id'] = $id;
        $project['created_at'] = (string) ($existing['created_at'] ?? $project['created_at'] ?? gmdate('c'));
        $project['updated_at'] = gmdate('c');
        $project['svgs'] = is_array($project['svgs'] ?? null) ? $project['svgs'] : [];
        $project['slides'] = is_array($project['slides'] ?? null) ? array_values($project['slides']) : [];

        $project['eight_confirmations'] = is_array($project['eight_confirmations'] ?? null) ? $project['eight_confirmations'] : [];
        $project['design_spec'] = is_array($project['design_spec'] ?? null) ? $project['design_spec'] : [];

        // Persistence is best-effort: a read-only / unavailable storage disk must never break planning.
        self::$memory[$id] = $project;
        try {
            $this->write('projects/'.$id.'.json', self::encode($project));
            $index = array_values(array_filter($this->readIndex(), static fn ($s) => ($s['id'] ?? '') !== $id));
            $index[] = $this->summary($project);
            $this->writeIndex($index);
        } catch (\Throwable $e) {
            // keep the in-memory copy
        }

        return $project;
    }

    public function setSvg(string $id, int $slideNumber, string $svg, ?array $slide = null): ?array
    {
        $project = $this->get($id);
        if ($project === null) {
            return null;
        }
        $project['svgs'][(string) $slideNumber] = $svg;
        if ($slide !== null) {
            foreach ($project['slides'] as $i => $s) {
                if ((int) ($s['slide_number'] ?? 0) === $slideNumber) {
                    $project['slides'][$i] = array_replace($s, $slide);
                }
            }
        }
        if (count($project['svgs']) >= count($project['slides']) && $project['slides'] !== []) {
            $project['status'] = 'done';
        } elseif (($project['status'] ?? '') !== 'done') {
            $project['status'] = 'generating';
        }

        return $this->save($project);
    }

    public function delete(string $id): bool
    {
        $id = self::safeId($id);
        if ($id === '') {
            return false;
        }
        $path = 'projects/'.$id.'.json';
        unset(self::$memory[$id]);
        try {
            $disk = $this->disk();
            if ($disk !== null && $disk->exists($path)) {
                $disk->delete($path);
            }
        } catch (\Throwable $e) {
            // ignore storage failures
        }
        $this->writeIndex(array_values(array_filter($this->readIndex(), static fn ($s) => ($s['id'] ?? '') !== $id)));

        return true;
    }

    private bool $seeding = false;

    private function seedIfEmpty(): void
    {
        if ($this->seeding || $this->disk() === null || $this->readIndex() !== []) {
            return;
        }
        $this->seeding = true;
        try {
            $this->seed();
        } catch (\Throwable $e) {
            // sample decks are optional
        } finally {
            $this->seeding = false;
        }
    }

    private function seed(): void
    {
        $prompts = new PromptRepository;
        $strategist = new StrategistService($prompts);
        $samples = [
            ['example-rakuten-mobile', 'Rakuten Mobile 5G network roadmap', 'rakuten-mobile', 'ppt169', 'pyramid', 6],
            ['example-dark-tech', 'Platform engineering: from tickets to self-service', 'dark-tech', 'ppt169', 'briefing', 5],
        ];
        foreach ($samples as [$id, $topic, $brandId, $format, $mode, $count]) {
            $brand = $prompts->brand($brandId);
            $canvas = $prompts->canvas($format);
            $plan = $strategist->synthesize($topic, $brand, $canvas, $count, $mode, '');
            $svgs = [];
            $total = count($plan['slides']);
            foreach ($plan['slides'] as $i => &$slide) {
                $slide['slide_number'] = $i + 1;
                $svgs[(string) ($i + 1)] = \Integrations\PptMaster\Support\SvgFinalizer::renderDeterministicSvg($slide, $plan['design_spec'], $format, $total);
            }
            unset($slide);
            $this->save([
                'id' => $id,
                'topic' => $topic,
                'source_material' => '',
                'brand' => $brandId,
                'canvas_format' => $format,
                'model' => 'built-in sample',
                'structure_mode' => $mode,
                'slide_count' => $total,
                'status' => 'done',
                'is_example' => true,
                'eight_confirmations' => $plan['eight_confirmations'],
                'design_spec' => $plan['design_spec'],
                'slides' => $plan['slides'],
                'svgs' => $svgs,
            ]);
        }
    }
}
