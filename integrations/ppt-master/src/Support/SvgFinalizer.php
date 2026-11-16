<?php

namespace Integrations\PptMaster\Support;

/**
 * PHP port of the essentials of ppt-master's finalize_svg / svg_quality_checker plus a
 * deterministic slide renderer used when the LLM output is unusable.
 */
final class SvgFinalizer
{
    /** @var array<string, array{0: int, 1: int}> */
    private const SIZES = ['ppt169' => [1280, 720], 'ppt43' => [1024, 768], 'xiaohongshu' => [1242, 1660]];

    /** @return array{0: int, 1: int} */
    public static function size(string $canvasFormat): array
    {
        return self::SIZES[$canvasFormat] ?? self::SIZES['ppt169'];
    }

    /** Extract the first <svg>...</svg> block from raw LLM output (null if none). */
    public static function extract(string $raw): ?string
    {
        if (preg_match('/<svg\b[\s\S]*<\/svg>/i', $raw, $m) === 1) {
            return $m[0];
        }

        return null;
    }

    /**
     * Repair and validate an SVG; returns the cleaned SVG or null when it cannot be made valid.
     *
     * @param  list<string>  $warnings
     */
    public static function repair(string $raw, string $canvasFormat, array &$warnings = []): ?string
    {
        $svg = self::extract($raw);
        if ($svg === null) {
            $warnings[] = 'No <svg> element found.';

            return null;
        }
        [$w, $h] = self::size($canvasFormat);

        $svg = preg_replace('/<script\b[\s\S]*?<\/script>/i', '', $svg) ?? $svg;
        $svg = preg_replace('/<foreignObject\b[\s\S]*?<\/foreignObject>/i', '', $svg) ?? $svg;
        $svg = preg_replace('/\son[a-z]+\s*=\s*("[^"]*"|\'[^\']*\')/i', '', $svg) ?? $svg;
        $svg = preg_replace('/&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)/', '&amp;', $svg) ?? $svg;

        $before = $svg;
        $svg = preg_replace('/<image\b[^>]*(?:xlink:)?href\s*=\s*["\']\s*https?:[^>]*>/i', '', $svg) ?? $svg;
        if ($svg !== $before) {
            $warnings[] = 'Removed external <image> references.';
        }

        // Expand-free handling of <use>: strip broken refs (symbol/use is not supported by the exporter).
        $svg = preg_replace('/<use\b[^>]*\/>/i', '', $svg) ?? $svg;
        $svg = preg_replace('/<use\b[^>]*>[\s\S]*?<\/use>/i', '', $svg) ?? $svg;

        // Root attributes.
        if (preg_match('/<svg\b[^>]*>/i', $svg, $m) === 1) {
            $root = $m[0];
            $newRoot = $root;
            if (stripos($newRoot, 'xmlns=') === false) {
                $newRoot = preg_replace('/<svg\b/i', '<svg xmlns="http://www.w3.org/2000/svg"', $newRoot, 1) ?? $newRoot;
            }
            if (stripos($newRoot, 'viewBox=') === false) {
                $newRoot = preg_replace('/<svg\b/i', '<svg viewBox="0 0 '.$w.' '.$h.'"', $newRoot, 1) ?? $newRoot;
            }
            if (preg_match('/\swidth\s*=/i', $newRoot) !== 1) {
                $newRoot = preg_replace('/<svg\b/i', '<svg width="'.$w.'"', $newRoot, 1) ?? $newRoot;
            }
            if (preg_match('/\sheight\s*=/i', $newRoot) !== 1) {
                $newRoot = preg_replace('/<svg\b/i', '<svg height="'.$h.'"', $newRoot, 1) ?? $newRoot;
            }
            $svg = str_replace($root, $newRoot, $svg);
        }

        if (! self::isWellFormed($svg)) {
            $warnings[] = 'SVG is not well-formed XML.';

            return null;
        }

        return $svg;
    }

    public static function isWellFormed(string $svg): bool
    {
        if (! class_exists(\DOMDocument::class)) {
            return true;
        }
        $prev = libxml_use_internal_errors(true);
        $doc = new \DOMDocument;
        $ok = $doc->loadXML($svg, LIBXML_NOERROR | LIBXML_NOWARNING);
        libxml_clear_errors();
        libxml_use_internal_errors($prev);

        return $ok !== false && $doc->documentElement !== null && strtolower($doc->documentElement->localName) === 'svg';
    }

    /**
     * Finalize an LLM-produced SVG; falls back to the deterministic renderer.
     *
     * @param  array<string, mixed>  $slidePlan
     * @param  array<string, mixed>  $designSpec
     * @return array{svg: string, valid: bool, warnings: list<string>, fallback: bool}
     */
    public static function finalize(string $raw, string $canvasFormat, array $slidePlan = [], array $designSpec = [], int $totalSlides = 1): array
    {
        $warnings = [];
        $svg = $raw === '' ? null : self::repair($raw, $canvasFormat, $warnings);
        if ($svg !== null) {
            return ['svg' => $svg, 'valid' => true, 'warnings' => $warnings, 'fallback' => false];
        }
        if ($slidePlan === []) {
            return ['svg' => '', 'valid' => false, 'warnings' => $warnings, 'fallback' => false];
        }
        $warnings[] = 'Used deterministic renderer.';

        return [
            'svg' => self::renderDeterministicSvg($slidePlan, $designSpec, $canvasFormat, $totalSlides),
            'valid' => true,
            'warnings' => $warnings,
            'fallback' => true,
        ];
    }

    // ------------------------------------------------------------------ rendering

    private static function e(mixed $v): string
    {
        return htmlspecialchars((string) $v, ENT_XML1 | ENT_QUOTES, 'UTF-8');
    }

    private static function hex(mixed $v, string $fallback): string
    {
        $v = is_string($v) ? trim($v) : '';

        return preg_match('/^#[0-9a-fA-F]{6}$/', $v) === 1 ? strtoupper($v) : $fallback;
    }

    /** Relative luminance contrast helper: choose readable text colour for a background. */
    private static function onColor(string $bg, string $light = '#FFFFFF', string $dark = '#111111'): string
    {
        $r = hexdec(substr($bg, 1, 2)) / 255;
        $g = hexdec(substr($bg, 3, 2)) / 255;
        $b = hexdec(substr($bg, 5, 2)) / 255;
        $lum = 0.2126 * $r + 0.7152 * $g + 0.0722 * $b;

        return $lum > 0.55 ? $dark : $light;
    }

    /**
     * Greedy word wrap using an average glyph width estimate.
     *
     * @return list<string>
     */
    public static function wrap(string $text, float $maxWidth, float $fontSize, int $maxLines = 6): array
    {
        $text = trim(preg_replace('/\s+/u', ' ', $text) ?? $text);
        if ($text === '') {
            return [];
        }
        $avg = $fontSize * 0.54;
        $lines = [];
        $cur = '';
        $width = static function (string $s) use ($avg): float {
            $w = 0.0;
            foreach (preg_split('//u', $s, -1, PREG_SPLIT_NO_EMPTY) ?: [] as $ch) {
                $w += strlen($ch) > 1 ? $avg * 1.8 : $avg;
            }

            return $w;
        };
        $words = preg_split('/(?<=\s)|(?<=[\x{3000}-\x{9FFF}])/u', $text, -1, PREG_SPLIT_NO_EMPTY) ?: [$text];
        foreach ($words as $word) {
            $try = $cur.$word;
            if ($cur !== '' && $width(rtrim($try)) > $maxWidth) {
                $lines[] = rtrim($cur);
                $cur = $word;
            } else {
                $cur = $try;
            }
        }
        if (trim($cur) !== '') {
            $lines[] = rtrim($cur);
        }
        if (count($lines) > $maxLines) {
            $lines = array_slice($lines, 0, $maxLines);
            $last = rtrim($lines[$maxLines - 1], " .,;:");
            $lines[$maxLines - 1] = $last.'…';
        }

        return $lines;
    }

    /**
     * @param  list<string>  $lines
     */
    private static function textBlock(array $lines, float $x, float $y, float $size, string $fill, string $font, float $lh = 1.35, string $weight = '400', string $anchor = 'start'): string
    {
        if ($lines === []) {
            return '';
        }
        $out = '<text x="'.round($x, 1).'" y="'.round($y, 1).'" font-family="'.self::e($font).'" font-size="'.round($size, 1).'" font-weight="'.$weight.'" fill="'.$fill.'" text-anchor="'.$anchor.'">';
        foreach ($lines as $i => $line) {
            $out .= '<tspan x="'.round($x, 1).'"'.($i === 0 ? '' : ' dy="'.round($size * $lh, 1).'"').'>'.self::e($line).'</tspan>';
        }

        return $out.'</text>';
    }

    /**
     * @param  array<string, mixed>  $slide
     * @param  array<string, mixed>  $design
     */
    public static function renderDeterministicSvg(array $slide, array $design, string $canvasFormat, int $totalSlides): string
    {
        [$W, $H] = self::size($canvasFormat);
        $c = is_array($design['colors'] ?? null) ? $design['colors'] : [];
        $f = is_array($design['fonts'] ?? null) ? $design['fonts'] : [];
        $primary = self::hex($c['primary'] ?? null, '#2563EB');
        $secondary = self::hex($c['secondary'] ?? null, '#0F172A');
        $accent = self::hex($c['accent'] ?? null, '#F59E0B');
        $bg = self::hex($c['background'] ?? null, '#FFFFFF');
        $surface = self::hex($c['surface'] ?? null, '#F5F5F5');
        $text = self::hex($c['text'] ?? null, '#1A1A1A');
        $muted = self::hex($c['muted'] ?? null, '#666666');
        $titleFont = (string) ($f['title_font'] ?? 'Helvetica Neue, Arial, sans-serif');
        $bodyFont = (string) ($f['body_font'] ?? 'Helvetica Neue, Arial, sans-serif');
        $titleFont = str_replace('"', '', $titleFont);
        $bodyFont = str_replace('"', '', $bodyFont);

        $n = (int) ($slide['slide_number'] ?? 1);
        $title = (string) ($slide['title'] ?? 'Untitled');
        $subtitle = (string) ($slide['subtitle'] ?? '');
        $type = (string) ($slide['page_type'] ?? 'content');
        $layout = strtolower((string) ($slide['layout_pattern'] ?? 'two-column'));
        $key = (string) ($slide['key_message'] ?? '');
        $bullets = array_values(array_filter(array_map('strval', (array) ($slide['bullets'] ?? [])), static fn ($b) => trim($b) !== ''));
        $metrics = array_values(array_filter((array) ($slide['metrics'] ?? []), 'is_array'));

        $M = 60.0;
        $sc = $W / 1280;
        $portrait = $H > $W;
        $out = '<svg xmlns="http://www.w3.org/2000/svg" width="'.$W.'" height="'.$H.'" viewBox="0 0 '.$W.' '.$H.'">';

        // ---- full-bleed pages: cover / section / closing
        if ($type === 'cover' || $type === 'closing' || $type === 'section' || $layout === 'hero' || $layout === 'closing') {
            $dark = $type !== 'section';
            $bgFill = $dark ? $primary : $surface;
            $fg = $dark ? self::onColor($primary) : $text;
            $out .= '<rect x="0" y="0" width="'.$W.'" height="'.$H.'" fill="'.$bgFill.'"/>';
            if ($dark) {
                $out .= '<polygon points="'.round($W * 0.62).',0 '.$W.',0 '.$W.','.$H.' '.round($W * 0.42).','.$H.'" fill="'.$secondary.'" fill-opacity="0.28"/>';
                $out .= '<circle cx="'.round($W * 0.88).'" cy="'.round($H * 0.22).'" r="'.round(120 * $sc).'" fill="'.$accent.'" fill-opacity="0.35"/>';
            }
            $out .= '<rect x="'.$M.'" y="'.round($H * 0.30).'" width="'.round(90 * $sc).'" height="'.round(8 * $sc).'" rx="4" fill="'.($dark ? $accent : $primary).'"/>';
            $ts = ($portrait ? 64 : 56) * $sc;
            $lines = self::wrap($title, $W - 2 * $M, $ts * 0.95, 3);
            $out .= self::textBlock($lines, $M, $H * 0.30 + 90 * $sc, $ts, $fg, $titleFont, 1.18, '700');
            $yy = $H * 0.30 + 90 * $sc + count($lines) * $ts * 1.18 + 20 * $sc;
            $sub = $subtitle !== '' ? $subtitle : $key;
            $sl = self::wrap($sub, $W - 2 * $M - 80 * $sc, 26 * $sc, 3);
            $out .= self::textBlock($sl, $M, $yy + 20 * $sc, 26 * $sc, $fg, $bodyFont, 1.4, '400');
            if ($type === 'section') {
                $out .= '<text x="'.($W - $M).'" y="'.round($H * 0.45).'" font-family="'.self::e($titleFont).'" font-size="'.round(200 * $sc).'" font-weight="700" fill="'.$primary.'" fill-opacity="0.15" text-anchor="end">'.sprintf('%02d', $n).'</text>';
            }
            if ($type === 'closing' && $bullets !== []) {
                $by = $yy + 100 * $sc;
                foreach (array_slice($bullets, 0, 3) as $i => $b) {
                    $out .= '<circle cx="'.($M + 8).'" cy="'.round($by + $i * 40 * $sc - 7).'" r="6" fill="'.$accent.'"/>';
                    $out .= self::textBlock(self::wrap($b, $W - 2 * $M - 40, 22 * $sc, 1), $M + 28, $by + $i * 40 * $sc, 22 * $sc, $fg, $bodyFont);
                }
            }
            $out .= self::footer($W, $H, $M, $n, $totalSlides, $fg, $bodyFont, $sc);

            return $out.'</svg>';
        }

        // ---- content page chrome
        $out .= '<rect x="0" y="0" width="'.$W.'" height="'.$H.'" fill="'.$bg.'"/>';
        $out .= '<rect x="0" y="0" width="'.round(14 * $sc).'" height="'.$H.'" fill="'.$primary.'"/>';
        $badge = strtoupper($type);
        $bw = round((strlen($badge) * 9 + 28) * $sc);
        $out .= '<rect x="'.$M.'" y="'.round(34 * $sc).'" width="'.$bw.'" height="'.round(26 * $sc).'" rx="'.round(13 * $sc).'" fill="'.$primary.'"/>';
        $out .= '<text x="'.round($M + $bw / 2).'" y="'.round(52 * $sc).'" font-family="'.self::e($bodyFont).'" font-size="'.round(13 * $sc).'" font-weight="700" fill="'.self::onColor($primary).'" text-anchor="middle">'.self::e($badge).'</text>';
        $tl = self::wrap($title, $W - 2 * $M, 38 * $sc * 0.95, 2);
        $out .= self::textBlock($tl, $M, 108 * $sc, 38 * $sc, $text, $titleFont, 1.2, '700');
        $top = 108 * $sc + count($tl) * 38 * $sc * 1.2 + 6 * $sc;
        if ($key !== '') {
            $kl = self::wrap($key, $W - 2 * $M, 20 * $sc, 2);
            $out .= self::textBlock($kl, $M, $top + 14 * $sc, 20 * $sc, $muted, $bodyFont, 1.35);
            $top += count($kl) * 20 * $sc * 1.35 + 20 * $sc;
        }
        $top += 16 * $sc;
        $bottom = $H - 70 * $sc;
        $areaW = $W - 2 * $M;
        $areaH = $bottom - $top;

        if ($type === 'toc') {
            $items = $bullets !== [] ? $bullets : [$title];
            $rows = min(count($items), 8);
            $rh = min(64 * $sc, $areaH / max($rows, 1));
            foreach (array_slice($items, 0, 8) as $i => $item) {
                $y = $top + $i * $rh;
                $out .= '<rect x="'.$M.'" y="'.round($y).'" width="'.round($areaW).'" height="'.round($rh - 10 * $sc).'" rx="10" fill="'.$surface.'"/>';
                $out .= '<text x="'.round($M + 24 * $sc).'" y="'.round($y + ($rh - 10 * $sc) / 2 + 9 * $sc).'" font-family="'.self::e($titleFont).'" font-size="'.round(28 * $sc).'" font-weight="700" fill="'.$primary.'">'.sprintf('%02d', $i + 1).'</text>';
                $out .= '<text x="'.round($M + 90 * $sc).'" y="'.round($y + ($rh - 10 * $sc) / 2 + 8 * $sc).'" font-family="'.self::e($bodyFont).'" font-size="'.round(22 * $sc).'" fill="'.$text.'">'.self::e(mb_substr($item, 0, 80)).'</text>';
            }
        } elseif ($layout === 'kpi-grid' || $layout === 'big-number' || ($metrics !== [] && $bullets === [])) {
            $metrics = $metrics ?: [['label' => $key ?: $title, 'value' => '—', 'trend' => '']];
            $cnt = min(count($metrics), 6);
            $cols = $layout === 'big-number' ? 1 : ($cnt <= 2 ? $cnt : ($cnt === 4 ? 2 : 3));
            if ($portrait) {
                $cols = min($cols, 2);
            }
            $rowsN = (int) ceil($cnt / $cols);
            $gap = 24 * $sc;
            $cw = ($areaW - $gap * ($cols - 1)) / $cols;
            $ch = min(260 * $sc, ($areaH - ($bullets !== [] ? 90 * $sc : 0) - $gap * ($rowsN - 1)) / $rowsN);
            foreach (array_slice($metrics, 0, 6) as $i => $m) {
                $x = $M + ($i % $cols) * ($cw + $gap);
                $y = $top + intdiv($i, $cols) * ($ch + $gap);
                $out .= self::kpiCard($x, $y, $cw, $ch, (string) ($m['value'] ?? ''), (string) ($m['label'] ?? ''), (string) ($m['trend'] ?? ''), $primary, $accent, $surface, $text, $muted, $titleFont, $bodyFont, $sc);
            }
            if ($bullets !== []) {
                $by = $top + $rowsN * ($ch + $gap) + 12 * $sc;
                foreach (array_slice($bullets, 0, 3) as $i => $b) {
                    $out .= '<circle cx="'.round($M + 7).'" cy="'.round($by + $i * 32 * $sc - 6).'" r="5" fill="'.$primary.'"/>';
                    $out .= self::textBlock(self::wrap($b, $areaW - 30, 20 * $sc, 1), $M + 24, $by + $i * 32 * $sc, 20 * $sc, $text, $bodyFont);
                }
            }
        } elseif ($layout === 'three-card' || $layout === 'four-quadrant') {
            $items = $bullets;
            if ($metrics !== []) {
                foreach ($metrics as $m) {
                    $items[] = trim(($m['label'] ?? '').': '.($m['value'] ?? ''));
                }
            }
            $items = $items ?: [$key ?: $title];
            $q = $layout === 'four-quadrant';
            $cnt = $q ? min(4, max(count($items), 4)) : min(3, max(count($items), 3));
            $cols = $q ? 2 : ($portrait ? 1 : 3);
            $rowsN = (int) ceil($cnt / $cols);
            $gap = 24 * $sc;
            $cw = ($areaW - $gap * ($cols - 1)) / $cols;
            $ch = min(($areaH - $gap * ($rowsN - 1)) / $rowsN, 300 * $sc);
            for ($i = 0; $i < $cnt; $i++) {
                $x = $M + ($i % $cols) * ($cw + $gap);
                $y = $top + intdiv($i, $cols) * ($ch + $gap);
                $item = $items[$i] ?? '';
                $out .= '<rect x="'.round($x).'" y="'.round($y).'" width="'.round($cw).'" height="'.round($ch).'" rx="14" fill="'.$surface.'"/>';
                $out .= '<rect x="'.round($x).'" y="'.round($y).'" width="'.round($cw).'" height="'.round(6 * $sc).'" rx="3" fill="'.$primary.'"/>';
                $out .= '<circle cx="'.round($x + 40 * $sc).'" cy="'.round($y + 52 * $sc).'" r="'.round(22 * $sc).'" fill="'.$primary.'"/>';
                $out .= '<text x="'.round($x + 40 * $sc).'" y="'.round($y + 60 * $sc).'" font-family="'.self::e($titleFont).'" font-size="'.round(22 * $sc).'" font-weight="700" fill="'.self::onColor($primary).'" text-anchor="middle">'.($i + 1).'</text>';
                $max = max(3, (int) floor(($ch - 110 * $sc) / (22 * $sc * 1.35)));
                $out .= self::textBlock(self::wrap($item, $cw - 56 * $sc, 20 * $sc, $max), $x + 28 * $sc, $y + 104 * $sc, 20 * $sc, $text, $bodyFont, 1.35);
            }
        } elseif ($layout === 'process-flow' || $type === 'timeline') {
            $steps = $bullets ?: [$key ?: $title];
            $cnt = min(count($steps), 6);
            $gap = 14 * $sc;
            if ($portrait) {
                $sh = ($areaH - $gap * ($cnt - 1)) / $cnt;
                foreach (array_slice($steps, 0, 6) as $i => $s) {
                    $y = $top + $i * ($sh + $gap);
                    $out .= '<rect x="'.$M.'" y="'.round($y).'" width="'.round($areaW).'" height="'.round($sh).'" rx="12" fill="'.$surface.'"/>';
                    $out .= '<rect x="'.$M.'" y="'.round($y).'" width="'.round(10 * $sc).'" height="'.round($sh).'" rx="5" fill="'.$primary.'"/>';
                    $out .= self::textBlock(self::wrap($s, $areaW - 80 * $sc, 20 * $sc, 3), $M + 36 * $sc, $y + 40 * $sc, 20 * $sc, $text, $bodyFont);
                }
            } else {
                $sw = ($areaW - $gap * ($cnt - 1)) / $cnt;
                $lineY = $top + 54 * $sc;
                $out .= '<line x1="'.$M.'" y1="'.round($lineY).'" x2="'.round($W - $M).'" y2="'.round($lineY).'" stroke="'.$primary.'" stroke-width="3" stroke-opacity="0.35"/>';
                foreach (array_slice($steps, 0, 6) as $i => $s) {
                    $x = $M + $i * ($sw + $gap);
                    $out .= '<circle cx="'.round($x + 30 * $sc).'" cy="'.round($lineY).'" r="'.round(26 * $sc).'" fill="'.$primary.'"/>';
                    $out .= '<text x="'.round($x + 30 * $sc).'" y="'.round($lineY + 8 * $sc).'" font-family="'.self::e($titleFont).'" font-size="'.round(22 * $sc).'" font-weight="700" fill="'.self::onColor($primary).'" text-anchor="middle">'.($i + 1).'</text>';
                    $out .= '<rect x="'.round($x).'" y="'.round($lineY + 50 * $sc).'" width="'.round($sw).'" height="'.round($bottom - $lineY - 60 * $sc).'" rx="12" fill="'.$surface.'"/>';
                    $out .= self::textBlock(self::wrap($s, $sw - 32 * $sc, 19 * $sc, 7), $x + 16 * $sc, $lineY + 90 * $sc, 19 * $sc, $text, $bodyFont, 1.35);
                }
            }
        } elseif ($layout === 'comparison-table' || $type === 'comparison') {
            $half = (int) ceil(max(count($bullets), 2) / 2);
            $left = array_slice($bullets, 0, $half);
            $right = array_slice($bullets, $half);
            $gap = 28 * $sc;
            $cw = ($areaW - $gap) / 2;
            foreach ([[$left, 0, $primary, 'Option A'], [$right, 1, $secondary, 'Option B']] as [$col, $i, $colr, $lab]) {
                $x = $M + $i * ($cw + $gap);
                $out .= '<rect x="'.round($x).'" y="'.round($top).'" width="'.round($cw).'" height="'.round($areaH).'" rx="14" fill="'.$surface.'"/>';
                $out .= '<rect x="'.round($x).'" y="'.round($top).'" width="'.round($cw).'" height="'.round(48 * $sc).'" rx="14" fill="'.$colr.'"/>';
                $out .= '<text x="'.round($x + 24 * $sc).'" y="'.round($top + 32 * $sc).'" font-family="'.self::e($titleFont).'" font-size="'.round(20 * $sc).'" font-weight="700" fill="'.self::onColor($colr).'">'.$lab.'</text>';
                foreach ($col as $r => $b) {
                    $y = $top + 84 * $sc + $r * 62 * $sc;
                    if ($y > $bottom - 30) {
                        break;
                    }
                    $out .= '<circle cx="'.round($x + 30 * $sc).'" cy="'.round($y - 6 * $sc).'" r="6" fill="'.$colr.'"/>';
                    $out .= self::textBlock(self::wrap($b, $cw - 80 * $sc, 20 * $sc, 2), $x + 50 * $sc, $y, 20 * $sc, $text, $bodyFont);
                }
            }
        } else {
            // two-column default: bullets left, KPI/visual panel right
            $leftW = $metrics !== [] || $portrait ? ($portrait ? $areaW : $areaW * 0.58) : $areaW;
            $y = $top + 6 * $sc;
            foreach (array_slice($bullets ?: [$key ?: $title], 0, 6) as $b) {
                $lines = self::wrap($b, $leftW - 44 * $sc, 22 * $sc, 3);
                $out .= '<circle cx="'.round($M + 9).'" cy="'.round($y - 7 * $sc).'" r="'.round(6 * $sc).'" fill="'.$primary.'"/>';
                $out .= self::textBlock($lines, $M + 30 * $sc, $y, 22 * $sc, $text, $bodyFont, 1.35);
                $y += max(1, count($lines)) * 22 * $sc * 1.35 + 24 * $sc;
            }
            if ($metrics !== [] && ! $portrait) {
                $px = $M + $leftW + 28 * $sc;
                $pw = $areaW - $leftW - 28 * $sc;
                $cnt = min(count($metrics), 3);
                $ch = ($areaH - 18 * $sc * ($cnt - 1)) / $cnt;
                foreach (array_slice($metrics, 0, 3) as $i => $m) {
                    $out .= self::kpiCard($px, $top + $i * ($ch + 18 * $sc), $pw, $ch, (string) ($m['value'] ?? ''), (string) ($m['label'] ?? ''), (string) ($m['trend'] ?? ''), $primary, $accent, $surface, $text, $muted, $titleFont, $bodyFont, $sc);
                }
            }
        }

        $out .= self::footer($W, $H, $M, $n, $totalSlides, $muted, $bodyFont, $sc, $primary);

        return $out.'</svg>';
    }

    private static function kpiCard(float $x, float $y, float $w, float $h, string $value, string $label, string $trend, string $primary, string $accent, string $surface, string $text, string $muted, string $titleFont, string $bodyFont, float $sc): string
    {
        $o = '<rect x="'.round($x).'" y="'.round($y).'" width="'.round($w).'" height="'.round($h).'" rx="14" fill="'.$surface.'"/>';
        $o .= '<rect x="'.round($x).'" y="'.round($y + 18 * $sc).'" width="'.round(6 * $sc).'" height="'.round(max($h - 36 * $sc, 10)).'" rx="3" fill="'.$primary.'"/>';
        $vs = min(72 * $sc, $h * 0.38);
        while ($vs > 24 * $sc && strlen($value) * $vs * 0.58 > $w - 40 * $sc) {
            $vs -= 4 * $sc;
        }
        $o .= '<text x="'.round($x + 28 * $sc).'" y="'.round($y + $h * 0.46).'" font-family="'.self::e($titleFont).'" font-size="'.round($vs, 1).'" font-weight="700" fill="'.$primary.'">'.self::e($value).'</text>';
        $o .= self::textBlock(self::wrap($label, $w - 56 * $sc, 18 * $sc, 2), $x + 28 * $sc, $y + $h * 0.46 + 34 * $sc, 18 * $sc, $text, $bodyFont, 1.3);
        if ($trend !== '') {
            $up = preg_match('/^\s*(\+|up|▲|↑)/iu', $trend) === 1;
            $down = preg_match('/^\s*(-|−|down|▼|↓)/iu', $trend) === 1;
            $col = $up ? '#16A34A' : ($down ? '#DC2626' : $muted);
            $arrow = $up ? '▲' : ($down ? '▼' : '●');
            $o .= '<text x="'.round($x + 28 * $sc).'" y="'.round($y + $h - 22 * $sc).'" font-family="'.self::e($bodyFont).'" font-size="'.round(16 * $sc).'" font-weight="700" fill="'.$col.'">'.$arrow.' '.self::e(mb_substr($trend, 0, 28)).'</text>';
        }

        return $o;
    }

    private static function footer(int $W, int $H, float $M, int $n, int $total, string $fill, string $font, float $sc, ?string $line = null): string
    {
        $o = '';
        if ($line !== null) {
            $o .= '<line x1="'.$M.'" y1="'.round($H - 54 * $sc).'" x2="'.round($W - $M).'" y2="'.round($H - 54 * $sc).'" stroke="'.$line.'" stroke-opacity="0.3" stroke-width="1.5"/>';
        }
        $o .= '<text x="'.round($W - $M).'" y="'.round($H - 24 * $sc).'" font-family="'.self::e($font).'" font-size="'.round(14 * $sc).'" fill="'.$fill.'" text-anchor="end">'.$n.' / '.max($total, $n).'</text>';

        return $o;
    }
}
