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

    /** @return \Illuminate\Contracts\Filesystem\Filesystem */
    private function disk()
    {
        return Integration::for(PromptRepository::INTEGRATION)->storage();
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
        $disk = $this->disk();
        if (! $disk->exists(self::INDEX)) {
            return [];
        }
        $data = json_decode((string) $disk->get(self::INDEX), true);

        return is_array($data) ? array_values(array_filter($data, 'is_array')) : [];
    }

    /** @param  list<array<string, mixed>>  $index */
    private function writeIndex(array $index): void
    {
        $this->disk()->put(self::INDEX, json_encode(array_values($index), JSON_UNESCAPED_UNICODE));
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
        $disk = $this->disk();
        if (! $disk->exists($path)) {
            return null;
        }
        $data = json_decode((string) $disk->get($path), true);

        return is_array($data) ? $data : null;
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

        $this->disk()->put('projects/'.$id.'.json', json_encode($project, JSON_UNESCAPED_UNICODE));
        $index = array_values(array_filter($this->readIndex(), static fn ($s) => ($s['id'] ?? '') !== $id));
        $index[] = $this->summary($project);
        $this->writeIndex($index);

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
        $disk = $this->disk();
        $path = 'projects/'.$id.'.json';
        if ($disk->exists($path)) {
            $disk->delete($path);
        }
        $this->writeIndex(array_values(array_filter($this->readIndex(), static fn ($s) => ($s['id'] ?? '') !== $id)));

        return true;
    }

    private bool $seeding = false;

    private function seedIfEmpty(): void
    {
        if ($this->seeding || $this->readIndex() !== []) {
            return;
        }
        $this->seeding = true;
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
        $this->seeding = false;
    }
}
