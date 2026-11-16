import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const integrationDir = path.dirname(fileURLToPath(import.meta.url));
const sdkJs = path.resolve(
  process.env.SDK_JS_PATH || path.join(integrationDir, '../../sdk/js')
);
const { integrationViteConfig } = await import(
  pathToFileURL(path.join(sdkJs, 'vite-preset.js')).href
);

export default defineConfig(
  integrationViteConfig({ entry: 'src/main.jsx', plugins: [react()] })
);
