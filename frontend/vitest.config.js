import { defineConfig } from 'vitest/config';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [vue()],
  test: {
    globals: true,
    environment: 'happy-dom',
    include: ['src/**/*.{test,spec}.{js,ts}', 'tests/**/*.{test,spec}.{js,ts}'],
    setupFiles: ['./tests/setup.js'],
    // CSS is stubbed to an empty string by default, which is right for every
    // component that imports a stylesheet — but the gallery's foundations page
    // *reads* design-system.css (`?raw`) to derive its token planches, and its
    // guardrail would then check an empty file and pass vacuously. Processed
    // for that one file only.
    css: { include: [/design-system\.css/] },
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      include: ['src/stores/**/*.js', 'src/services/**/*.js', 'src/schemas/**/*.js']
    }
  },
  resolve: {
    alias: {
      '@': resolve(__dirname, './src')
    }
  }
});
