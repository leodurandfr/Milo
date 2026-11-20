import { defineConfig } from 'vitest/config';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));

// One time zone for every run, and one with a daylight-saving change, so a
// date computation that only holds on 24-hour days fails on CI (which runs in
// UTC) as it would on a unit. Set before the workers start: they inherit it.
process.env.TZ = 'Europe/Paris';

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
