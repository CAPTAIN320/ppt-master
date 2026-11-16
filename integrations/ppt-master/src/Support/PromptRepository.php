<?php

namespace Integrations\PptMaster\Support;

use Analyzer\IntegrationSdk\Integration;

/**
 * Loads prompt/brand resources and exposes brand + canvas metadata.
 */
final class PromptRepository
{
    public const INTEGRATION = 'ppt-master';

    /** @var array<string, array<string, mixed>> */
    private const BRANDS = [
        'rakuten-mobile' => [
            'name' => 'Rakuten Mobile',
            'description' => 'Tech-forward telecom brand: magenta on white with black structure.',
            'file' => 'rakuten_mobile',
            'colors' => ['primary' => '#FF008C', 'secondary' => '#000000', 'accent' => '#777B7E', 'background' => '#FFFFFF', 'surface' => '#F5F5F5', 'text' => '#1A1A1A', 'muted' => '#666666'],
            'fonts' => ['title_font' => 'Rakuten Sans, Helvetica Neue, Arial, sans-serif', 'body_font' => 'Rakuten Sans, Helvetica Neue, Arial, sans-serif'],
            'tone' => 'Tech-forward, modern, confident, network-centric.',
        ],
        'rakuten-crimson' => [
            'name' => 'Rakuten Crimson',
            'description' => 'Rakuten Group corporate identity: bold crimson, black and white.',
            'file' => 'rakuten_crimson',
            'colors' => ['primary' => '#BF0000', 'secondary' => '#000000', 'accent' => '#777B7E', 'background' => '#FFFFFF', 'surface' => '#F5F5F5', 'text' => '#1A1A1A', 'muted' => '#666666'],
            'fonts' => ['title_font' => 'Rakuten Sans, Helvetica Neue, Arial, sans-serif', 'body_font' => 'Rakuten Sans, Helvetica Neue, Arial, sans-serif'],
            'tone' => 'Bold, energetic, corporate; Empowering people.',
        ],
        'anthropic' => [
            'name' => 'Anthropic',
            'description' => 'Warm terracotta on soft cream, editorial and calm.',
            'file' => 'anthropic',
            'colors' => ['primary' => '#D97757', 'secondary' => '#141413', 'accent' => '#6A9BCC', 'background' => '#FAF9F5', 'surface' => '#F0EFEA', 'text' => '#141413', 'muted' => '#6B6A64'],
            'fonts' => ['title_font' => 'Georgia, Times New Roman, serif', 'body_font' => 'Helvetica Neue, Arial, sans-serif'],
            'tone' => 'Thoughtful, human, editorial.',
        ],
        'google' => [
            'name' => 'Google',
            'description' => 'Four-colour Google palette on clean white.',
            'file' => 'google',
            'colors' => ['primary' => '#4285F4', 'secondary' => '#34A853', 'accent' => '#FBBC05', 'background' => '#FFFFFF', 'surface' => '#F8F9FA', 'text' => '#202124', 'muted' => '#5F6368'],
            'fonts' => ['title_font' => 'Arial, Helvetica, sans-serif', 'body_font' => 'Arial, Helvetica, sans-serif'],
            'tone' => 'Friendly, clear, helpful.',
        ],
        'corporate-blue' => [
            'name' => 'Corporate Blue',
            'description' => 'Navy and azure corporate look for formal reports.',
            'file' => null,
            'colors' => ['primary' => '#00418D', 'secondary' => '#0070C0', 'accent' => '#F29F05', 'background' => '#FFFFFF', 'surface' => '#F4F6F9', 'text' => '#1F2937', 'muted' => '#6B7280'],
            'fonts' => ['title_font' => 'Arial, Helvetica, sans-serif', 'body_font' => 'Arial, Helvetica, sans-serif'],
            'tone' => 'Formal, trustworthy, precise.',
        ],
        'dark-tech' => [
            'name' => 'Dark Tech',
            'description' => 'Cyan and indigo on dark slate for technical decks.',
            'file' => null,
            'colors' => ['primary' => '#38BDF8', 'secondary' => '#818CF8', 'accent' => '#34D399', 'background' => '#0F172A', 'surface' => '#1E293B', 'text' => '#F8FAFC', 'muted' => '#94A3B8'],
            'fonts' => ['title_font' => 'Helvetica Neue, Arial, sans-serif', 'body_font' => 'Helvetica Neue, Arial, sans-serif'],
            'tone' => 'Technical, modern, precise.',
        ],
        'none' => [
            'name' => 'Custom / Auto',
            'description' => 'No brand: the AI Strategist selects a harmonious palette.',
            'file' => null,
            'colors' => ['primary' => '#2563EB', 'secondary' => '#0F172A', 'accent' => '#F59E0B', 'background' => '#FFFFFF', 'surface' => '#F1F5F9', 'text' => '#0F172A', 'muted' => '#64748B'],
            'fonts' => ['title_font' => 'Helvetica Neue, Arial, sans-serif', 'body_font' => 'Helvetica Neue, Arial, sans-serif'],
            'tone' => 'Neutral and professional.',
        ],
    ];

    /** @var array<string, array{label: string, width: int, height: int}> */
    private const CANVAS = [
        'ppt169' => ['label' => '16:9 Widescreen (1280x720)', 'width' => 1280, 'height' => 720],
        'ppt43' => ['label' => '4:3 Standard (1024x768)', 'width' => 1024, 'height' => 768],
        'xiaohongshu' => ['label' => '3:4 Portrait (1242x1660)', 'width' => 1242, 'height' => 1660],
    ];

    /**
     * Prompt template with {{placeholders}} replaced.
     *
     * @param  array<string, scalar|null>  $vars
     */
    public function prompt(string $name, array $vars = []): string
    {
        $text = $this->readResource('resources/prompts/'.basename($name));
        $search = [];
        $replace = [];
        foreach ($vars as $key => $value) {
            $search[] = '{{'.$key.'}}';
            $replace[] = (string) $value;
        }

        return $search === [] ? $text : str_replace($search, $replace, $text);
    }

    /** Markdown brand specification (empty when the brand has no file). */
    public function brandSpec(string $brandId): string
    {
        $brand = self::BRANDS[$brandId] ?? null;
        if ($brand === null || empty($brand['file'])) {
            return '';
        }
        $text = $this->readResource('resources/brands/'.$brand['file'].'.md');

        return mb_substr($text, 0, 6000);
    }

    /** @return array<string, mixed> */
    public function brand(string $brandId): array
    {
        $brand = self::BRANDS[$brandId] ?? self::BRANDS['none'];
        $brand['id'] = isset(self::BRANDS[$brandId]) ? $brandId : 'none';
        unset($brand['file']);

        return $brand;
    }

    /** @return list<array<string, mixed>> */
    public function brands(): array
    {
        $out = [];
        foreach (array_keys(self::BRANDS) as $id) {
            $out[] = $this->brand($id);
        }

        return $out;
    }

    /** @return list<array<string, mixed>> */
    public function canvasFormats(): array
    {
        $out = [];
        foreach (self::CANVAS as $id => $c) {
            $out[] = ['id' => $id] + $c;
        }

        return $out;
    }

    /** @return array{id: string, label: string, width: int, height: int} */
    public function canvas(string $id): array
    {
        $key = isset(self::CANVAS[$id]) ? $id : 'ppt169';

        return ['id' => $key] + self::CANVAS[$key];
    }

    private function readResource(string $relative): string
    {
        try {
            $path = Integration::for(self::INTEGRATION)->path($relative);
        } catch (\Throwable $e) {
            return '';
        }
        if (! is_file($path)) {
            return '';
        }
        $text = file_get_contents($path);

        return $text === false ? '' : $text;
    }
}
