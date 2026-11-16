<?php

namespace Integrations\PptMaster\Services;

use Integrations\PptMaster\Support\PromptRepository;
use Integrations\PptMaster\Support\SvgFinalizer;

/**
 * Generates one finalized SVG slide (LLM first, deterministic renderer as guaranteed fallback).
 */
final class ExecutorService
{
    public function __construct(
        private PromptRepository $prompts = new PromptRepository,
        private AiGateway $ai = new AiGateway,
    ) {
    }

    /**
     * @param  array<string, mixed>  $in
     * @return array{slide_number: int, svg: string, slide: array<string, mixed>, model: string, warnings: list<string>, fallback: bool}
     */
    public function slide(array $in): array
    {
        $plan = is_array($in['slide_plan'] ?? null) ? $in['slide_plan'] : [];
        $design = is_array($in['design_spec'] ?? null) ? $in['design_spec'] : [];
        $conf = is_array($in['eight_confirmations'] ?? null) ? $in['eight_confirmations'] : [];
        $format = (string) ($in['canvas_format'] ?? 'ppt169');
        $canvas = $this->prompts->canvas($format);
        $brand = $this->prompts->brand((string) ($in['brand'] ?? 'none'));
        $n = (int) ($in['slide_number'] ?? ($plan['slide_number'] ?? 1));
        $total = max($n, (int) ($in['total_slides'] ?? ($conf['page_count'] ?? $n)));
        if (! isset($design['colors'])) {
            $design['colors'] = $brand['colors'];
        }
        if (! isset($design['fonts'])) {
            $design['fonts'] = $brand['fonts'];
        }
        $plan['slide_number'] = $n;
        $c = $design['colors'];
        $f = $design['fonts'];
        $custom = trim((string) ($in['custom_instruction'] ?? ''));

        $system = $this->prompts->prompt('executor-system.md');
        $user = $this->prompts->prompt('executor-user.md', [
            'slide_number' => $n,
            'total_slides' => $total,
            'canvas_width' => $canvas['width'],
            'canvas_height' => $canvas['height'],
            'topic' => (string) ($in['topic'] ?? ''),
            'brand_name' => $brand['name'],
            'primary' => $c['primary'] ?? '', 'secondary' => $c['secondary'] ?? '', 'accent' => $c['accent'] ?? '',
            'background' => $c['background'] ?? '', 'surface' => $c['surface'] ?? '', 'text' => $c['text'] ?? '', 'muted' => $c['muted'] ?? '',
            'title_font' => $f['title_font'] ?? '', 'body_font' => $f['body_font'] ?? '',
            'core_conclusion' => (string) ($conf['core_conclusion'] ?? ''),
            'audience' => (string) ($conf['audience_and_scenario'] ?? ''),
            'slide_plan' => json_encode($plan, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT),
            'page_type' => (string) ($plan['page_type'] ?? 'content'),
            'layout_pattern' => (string) ($plan['layout_pattern'] ?? 'two-column'),
            'custom_instruction' => $custom !== '' ? 'Additional instruction from the user: '.$custom : '',
        ]);

        $raw = '';
        $model = (string) ($in['model'] ?? '');
        try {
            $reply = $this->ai->chat([
                ['role' => 'system', 'content' => $system !== '' ? $system : 'You write a single valid SVG slide. Output only the <svg> element.'],
                ['role' => 'user', 'content' => $user],
            ], $model, 0.5);
            $raw = $reply['content'];
            $model = $reply['model'];
        } catch (\Throwable $e) {
            $raw = '';
        }
        if (AiGateway::isFake($raw, $model)) {
            $raw = '';
        }

        $result = SvgFinalizer::finalize($raw, $format, $plan, $design, $total);

        return [
            'slide_number' => $n,
            'svg' => $result['svg'],
            'slide' => $plan + ['elements' => $this->elements($plan)],
            'model' => $model,
            'warnings' => $result['warnings'],
            'fallback' => $result['fallback'],
        ];
    }

    /**
     * Structured elements used by the client for native PowerPoint shape generation.
     *
     * @param  array<string, mixed>  $plan
     * @return list<array<string, mixed>>
     */
    private function elements(array $plan): array
    {
        $els = [['type' => 'title', 'text' => (string) ($plan['title'] ?? '')]];
        if (! empty($plan['subtitle'])) {
            $els[] = ['type' => 'subtitle', 'text' => (string) $plan['subtitle']];
        }
        if (! empty($plan['key_message'])) {
            $els[] = ['type' => 'key_message', 'text' => (string) $plan['key_message']];
        }
        foreach ((array) ($plan['bullets'] ?? []) as $b) {
            $els[] = ['type' => 'bullet', 'text' => (string) $b];
        }
        foreach ((array) ($plan['metrics'] ?? []) as $m) {
            if (is_array($m)) {
                $els[] = ['type' => 'metric', 'label' => (string) ($m['label'] ?? ''), 'value' => (string) ($m['value'] ?? ''), 'trend' => (string) ($m['trend'] ?? '')];
            }
        }

        return $els;
    }
}
