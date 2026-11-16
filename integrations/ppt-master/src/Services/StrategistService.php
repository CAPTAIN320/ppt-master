<?php

namespace Integrations\PptMaster\Services;

use Integrations\PptMaster\Support\PromptRepository;

/**
 * Produces the Eight Confirmations, design spec and slide blueprint.
 */
final class StrategistService
{
    public const PAGE_TYPES = ['cover', 'toc', 'section', 'content', 'data', 'comparison', 'timeline', 'closing'];

    public const LAYOUTS = ['hero', 'two-column', 'three-card', 'four-quadrant', 'kpi-grid', 'process-flow', 'comparison-table', 'big-number', 'closing'];

    public function __construct(
        private PromptRepository $prompts = new PromptRepository,
        private AiGateway $ai = new AiGateway,
    ) {
    }

    /**
     * @param  array<string, mixed>  $input
     * @return array{plan: array<string, mixed>, model: string, source: string}
     */
    public function plan(array $input): array
    {
        $topic = trim((string) ($input['topic'] ?? ''));
        $brandId = (string) ($input['brand'] ?? 'none');
        $brand = $this->prompts->brand($brandId);
        $canvas = $this->prompts->canvas((string) ($input['canvas_format'] ?? 'ppt169'));
        $count = max(3, min(30, (int) ($input['slide_count'] ?? 8)));
        $mode = (string) ($input['structure_mode'] ?? 'pyramid');
        $source = (string) ($input['source_material'] ?? '');

        $system = $this->prompts->prompt('strategist-system.md');
        $user = $this->prompts->prompt('strategist-user.md', [
            'topic' => $topic,
            'brand_name' => $brand['name'],
            'brand_spec' => $brandId === 'none' ? 'No brand: choose a harmonious palette.' : $this->brandBrief($brand, $brandId),
            'canvas_label' => $canvas['label'],
            'slide_count' => $count,
            'structure_mode' => $mode,
            'source_material' => mb_substr($source, 0, 12000),
        ]);

        $model = (string) ($input['model'] ?? '');
        $content = '';
        $usedModel = $model;
        try {
            $reply = $this->ai->chat([
                ['role' => 'system', 'content' => $system !== '' ? $system : 'You are a presentation strategist. Reply with JSON only.'],
                ['role' => 'user', 'content' => $user],
            ], $model, 0.4);
            $content = $reply['content'];
            $usedModel = $reply['model'];
        } catch (\Throwable $e) {
            $content = '';
        }

        $parsed = null;
        if (! AiGateway::isFake($content, $usedModel)) {
            $parsed = $this->extractJson($content);
        }
        $sourceKind = 'ai';
        if (! is_array($parsed) || empty($parsed['slides']) || ! is_array($parsed['slides'])) {
            $parsed = $this->synthesize($topic, $brand, $canvas, $count, $mode, $source);
            $sourceKind = 'synthesized';
        }

        return [
            'plan' => $this->normalize($parsed, $topic, $brand, $canvas, $count, $mode),
            'model' => $usedModel,
            'source' => $sourceKind,
        ];
    }

    /** @param  array<string, mixed>  $brand */
    private function brandBrief(array $brand, string $brandId): string
    {
        $spec = $this->prompts->brandSpec($brandId);
        $brief = 'Palette: '.json_encode($brand['colors']).'. Fonts: '.json_encode($brand['fonts']).'. Tone: '.$brand['tone'];

        return $spec !== '' ? $brief."\n\nBrand specification:\n".$spec : $brief;
    }

    /** @return array<string, mixed>|null */
    public function extractJson(string $text): ?array
    {
        $text = trim($text);
        if ($text === '') {
            return null;
        }
        if (preg_match('/```(?:json)?\s*([\s\S]*?)```/i', $text, $m) === 1) {
            $try = json_decode(trim($m[1]), true);
            if (is_array($try)) {
                return $try;
            }
        }
        $direct = json_decode($text, true);
        if (is_array($direct)) {
            return $direct;
        }
        $start = strpos($text, '{');
        $end = strrpos($text, '}');
        if ($start !== false && $end !== false && $end > $start) {
            $try = json_decode(substr($text, $start, $end - $start + 1), true);
            if (is_array($try)) {
                return $try;
            }
            // Partial output: try to close the structure progressively.
            $chunk = substr($text, $start);
            for ($cut = strlen($chunk); $cut > 200; $cut -= 1) {
                if ($chunk[$cut - 1] !== '}') {
                    continue;
                }
                $candidate = substr($chunk, 0, $cut);
                $open = substr_count($candidate, '[') - substr_count($candidate, ']');
                $openObj = substr_count($candidate, '{') - substr_count($candidate, '}');
                $try = json_decode($candidate.str_repeat(']', max(0, $open)).str_repeat('}', max(0, $openObj)), true);
                if (is_array($try) && ! empty($try['slides'])) {
                    return $try;
                }
                if ($cut < strlen($chunk) - 4000) {
                    break;
                }
            }
        }

        return null;
    }

    /**
     * @param  array<string, mixed>  $p
     * @param  array<string, mixed>  $brand
     * @param  array<string, mixed>  $canvas
     * @return array<string, mixed>
     */
    private function normalize(array $p, string $topic, array $brand, array $canvas, int $count, string $mode): array
    {
        $ec = is_array($p['eight_confirmations'] ?? null) ? $p['eight_confirmations'] : [];
        $keys = ['goal_and_type', 'audience_and_scenario', 'core_conclusion', 'structure_mode', 'page_count', 'style_and_theme', 'aspect_ratio', 'content_outline_summary'];
        $conf = [];
        foreach ($keys as $k) {
            $v = $ec[$k] ?? '';
            $conf[$k] = is_scalar($v) ? (string) $v : json_encode($v, JSON_UNESCAPED_UNICODE);
        }
        if ($conf['aspect_ratio'] === '') {
            $conf['aspect_ratio'] = $canvas['label'];
        }
        if ($conf['page_count'] === '') {
            $conf['page_count'] = (string) $count;
        }

        $ds = is_array($p['design_spec'] ?? null) ? $p['design_spec'] : [];
        $colors = [];
        foreach (['primary', 'secondary', 'accent', 'background', 'surface', 'text', 'muted'] as $k) {
            $v = $ds['colors'][$k] ?? null;
            $colors[$k] = is_string($v) && preg_match('/^#[0-9a-fA-F]{6}$/', trim($v)) === 1 ? strtoupper(trim($v)) : $brand['colors'][$k];
        }
        $fonts = [
            'title_font' => (string) (($ds['fonts']['title_font'] ?? '') ?: $brand['fonts']['title_font']),
            'body_font' => (string) (($ds['fonts']['body_font'] ?? '') ?: $brand['fonts']['body_font']),
        ];

        $slides = [];
        foreach (array_values((array) $p['slides']) as $i => $s) {
            if (! is_array($s)) {
                continue;
            }
            $type = in_array($s['page_type'] ?? '', self::PAGE_TYPES, true) ? $s['page_type'] : 'content';
            $layout = strtolower((string) ($s['layout_pattern'] ?? 'two-column'));
            if (! in_array($layout, self::LAYOUTS, true)) {
                $layout = 'two-column';
            }
            $metrics = [];
            foreach ((array) ($s['metrics'] ?? []) as $m) {
                if (is_array($m)) {
                    $metrics[] = ['label' => (string) ($m['label'] ?? ''), 'value' => (string) ($m['value'] ?? ''), 'trend' => (string) ($m['trend'] ?? '')];
                }
            }
            $slides[] = [
                'slide_number' => $i + 1,
                'title' => (string) ($s['title'] ?? 'Slide '.($i + 1)),
                'subtitle' => (string) ($s['subtitle'] ?? ''),
                'page_type' => $type,
                'layout_pattern' => $layout,
                'key_message' => (string) ($s['key_message'] ?? ''),
                'bullets' => array_values(array_map('strval', array_filter((array) ($s['bullets'] ?? []), 'is_scalar'))),
                'metrics' => $metrics,
                'visual_notes' => (string) ($s['visual_notes'] ?? ''),
                'speaker_notes' => (string) ($s['speaker_notes'] ?? ''),
            ];
        }

        return [
            'eight_confirmations' => $conf,
            'design_spec' => ['colors' => $colors, 'fonts' => $fonts],
            'slides' => $slides,
        ];
    }

    /**
     * Deterministic, topic-aware plan used when the model output is unusable (or in fake driver mode).
     *
     * @param  array<string, mixed>  $brand
     * @param  array<string, mixed>  $canvas
     * @return array<string, mixed>
     */
    public function synthesize(string $topic, array $brand, array $canvas, int $count, string $mode, string $source): array
    {
        $topic = $topic !== '' ? $topic : 'Strategic overview';
        $short = mb_strlen($topic) > 70 ? mb_substr($topic, 0, 67).'…' : $topic;

        // Pull candidate points from source material (first meaningful lines).
        $points = [];
        foreach (preg_split('/\R/u', $source) ?: [] as $line) {
            $line = trim(preg_replace('/^[#>\-\*\d\.\)\s]+/u', '', $line) ?? $line);
            if (mb_strlen($line) >= 12 && mb_strlen($line) <= 140) {
                $points[] = $line;
            }
            if (count($points) >= 40) {
                break;
            }
        }
        $pick = static function (int $i, string $fallback) use ($points): string {
            return $points[$i] ?? $fallback;
        };

        $themes = [
            ['Why this matters now', 'Context and urgency', ['The landscape is shifting faster than planning cycles', 'Stakeholders expect measurable progress', 'Delay increases cost and risk']],
            ['The core insight behind '.$short, 'What the evidence says', ['One root cause explains most of the gap', 'Data points to a repeatable pattern', 'Addressing it unlocks compounding gains']],
            ['Three pillars carry the strategy', 'How we deliver', ['Foundation: align people, data and tooling', 'Execution: ship in short, measurable cycles', 'Scale: standardise what works']],
            ['Impact shows up in the numbers', 'Measured outcomes', []],
            ['A phased roadmap makes delivery predictable', 'From pilot to scale', ['Discover and baseline', 'Pilot with one team', 'Expand to adjacent teams', 'Operate and optimise']],
            ['Options compared: build, buy or partner', 'Trade-offs at a glance', ['Build: full control, slower start', 'Build: higher long-term ownership cost', 'Buy: fast start, limited flexibility', 'Buy: predictable licence cost']],
            ['Risks are known and manageable', 'Mitigation plan', ['Adoption risk: mitigate with champions and training', 'Data risk: governance from day one', 'Delivery risk: weekly checkpoints', 'Budget risk: stage-gated funding']],
            ['What success looks like in twelve months', 'Targets and owners', []],
            ['Customer and user value comes first', 'Voice of the user', ['Clear problem statement validated with users', 'Prototype feedback shapes every release', 'Satisfaction tracked every sprint']],
            ['Governance keeps everyone aligned', 'Operating rhythm', ['Monthly steering review', 'Weekly delivery stand-up', 'Quarterly strategy refresh']],
        ];
        $layouts = ['two-column', 'three-card', 'two-column', 'kpi-grid', 'process-flow', 'comparison-table', 'four-quadrant', 'kpi-grid', 'two-column', 'three-card'];
        $metricSets = [
            [['label' => 'Baseline efficiency gain', 'value' => '+32%', 'trend' => '+8 pts QoQ'], ['label' => 'Cycle time', 'value' => '-41%', 'trend' => '-12 days'], ['label' => 'Adoption', 'value' => '87%', 'trend' => '+15 pts']],
            [['label' => 'Target ROI', 'value' => '3.2x', 'trend' => '+0.6x vs plan'], ['label' => 'Payback period', 'value' => '9 mo', 'trend' => '-3 mo'], ['label' => 'Satisfaction', 'value' => '4.6/5', 'trend' => '+0.4'], ['label' => 'Coverage', 'value' => '92%', 'trend' => '+11 pts']],
        ];

        $slides = [];
        $slides[] = ['page_type' => 'cover', 'layout_pattern' => 'hero', 'title' => $short, 'subtitle' => $brand['name'] !== 'Custom / Auto' ? $brand['name'].' · '.date('F Y') : date('F Y'), 'key_message' => 'A clear plan, backed by evidence, ready for decision.', 'bullets' => [], 'metrics' => [], 'visual_notes' => 'Full-bleed brand gradient with large title.', 'speaker_notes' => 'Welcome the audience, state the purpose and the decision we need today.'];
        $middle = max(1, $count - 2);
        $useToc = $count >= 8;
        if ($useToc) {
            $slides[] = ['page_type' => 'toc', 'layout_pattern' => 'two-column', 'title' => 'Agenda', 'subtitle' => '', 'key_message' => 'Five questions this briefing answers.', 'bullets' => ['Why this matters now', 'The core insight', 'Strategy and pillars', 'Impact and roadmap', 'Risks, success measures and next steps'], 'metrics' => [], 'visual_notes' => 'Numbered agenda rows.', 'speaker_notes' => 'Walk through the agenda in 20 seconds.'];
            $middle--;
        }
        for ($i = 0; $i < $middle; $i++) {
            $t = $themes[$i % count($themes)];
            $layout = $layouts[$i % count($layouts)];
            $bullets = $t[2];
            if ($bullets !== []) {
                $p = $pick($i, '');
                if ($p !== '') {
                    $bullets[0] = $p;
                }
            }
            $metrics = $layout === 'kpi-grid' ? $metricSets[$i % 2] : [];
            if ($i > 0 && $i % 4 === 0 && $middle > 6) {
                $slides[] = ['page_type' => 'section', 'layout_pattern' => 'hero', 'title' => 'Part '.(intdiv($i, 4) + 1).': '.$t[1], 'subtitle' => $t[0], 'key_message' => $t[1], 'bullets' => [], 'metrics' => [], 'visual_notes' => 'Section divider with large numeral.', 'speaker_notes' => 'Transition to the next part.'];
                $i++;
                if ($i >= $middle) {
                    break;
                }
            }
            $slides[] = [
                'page_type' => $layout === 'kpi-grid' ? 'data' : ($layout === 'process-flow' ? 'timeline' : ($layout === 'comparison-table' ? 'comparison' : 'content')),
                'layout_pattern' => $layout,
                'title' => $t[0],
                'subtitle' => $t[1],
                'key_message' => $t[0],
                'bullets' => $bullets,
                'metrics' => $metrics,
                'visual_notes' => 'Illustrative figures are placeholders; replace with source data.',
                'speaker_notes' => 'Make the point in one sentence, show the evidence, then bridge to the next slide.',
            ];
        }
        $slides = array_slice($slides, 0, max($count - 1, 2));
        $slides[] = ['page_type' => 'closing', 'layout_pattern' => 'closing', 'title' => 'Decision and next steps', 'subtitle' => 'Thank you', 'key_message' => 'Approve the pilot and name an owner this week.', 'bullets' => ['Approve scope and budget for the pilot', 'Nominate an executive sponsor', 'Kick off within two weeks'], 'metrics' => [], 'visual_notes' => 'Brand gradient closing slide.', 'speaker_notes' => 'Restate the conclusion, ask for the decision, open Q&A.'];

        $modeLabel = ucfirst($mode);
        $outline = [];
        foreach ($slides as $i => $s) {
            $outline[] = ($i + 1).'. '.$s['title'];
        }

        return [
            'eight_confirmations' => [
                'goal_and_type' => 'Persuade and align stakeholders on "'.$topic.'" (decision-oriented '.$mode.' deck).',
                'audience_and_scenario' => 'Business leaders and project owners, reviewed in a meeting and read afterwards.',
                'core_conclusion' => 'Act now on '.$short.' with a phased, measurable plan.',
                'structure_mode' => $modeLabel.' - conclusion first, then supporting arguments and evidence.',
                'page_count' => (string) count($slides).' slides: cover, '.($useToc ? 'agenda, ' : '').'content, closing.',
                'style_and_theme' => $brand['name'].' palette ('.$brand['colors']['primary'].' primary), clean cards, generous whitespace. '.$brand['tone'],
                'aspect_ratio' => $canvas['label'],
                'content_outline_summary' => implode("\n", array_slice($outline, 0, 6)),
            ],
            'design_spec' => ['colors' => $brand['colors'], 'fonts' => $brand['fonts']],
            'slides' => $slides,
        ];
    }
}
