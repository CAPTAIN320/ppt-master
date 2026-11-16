<?php

namespace Integrations\PptMaster\Services;

use Analyzer\IntegrationSdk\Integration;
use Integrations\PptMaster\Support\PromptRepository;

/**
 * Thin wrapper around the Analyzer AI client (model + max_tokens resolution).
 */
final class AiGateway
{
    /**
     * @param  array<int, array{role: string, content: string}>  $messages
     * @return array{content: string, model: string}
     */
    public function chat(array $messages, ?string $model = null, float $temperature = 0.4, ?int $maxTokens = null): array
    {
        $ctx = Integration::for(PromptRepository::INTEGRATION);
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

        $reply = $ai->chat($messages, [
            'model' => $resolved,
            'temperature' => $temperature,
            'max_tokens' => $max,
        ]);

        return [
            'content' => (string) ($reply['content'] ?? ''),
            'model' => (string) ($reply['model'] ?? $resolved),
        ];
    }

    /** @return array<string, mixed> */
    public function settings(): array
    {
        return Integration::for(PromptRepository::INTEGRATION)->settings();
    }

    /** @return array{models: array<int, mixed>, defaultModel: string} */
    public function models(): array
    {
        $ai = Integration::for(PromptRepository::INTEGRATION)->ai();
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
