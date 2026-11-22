import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'
import { thirdPartyNotices } from './vite-plugins/thirdPartyNotices.js'

export default defineConfig(({ mode }) => ({
  // thirdPartyNotices: the repo's THIRD_PARTY_NOTICES.md, copied into dist/.
  plugins: [vue(), thirdPartyNotices()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  define: {
    __VUE_OPTIONS_API__: false,
    __VUE_PROD_DEVTOOLS__: false,
  },
  build: {
    rollupOptions: {
      input: {
        // The app.
        main: path.resolve(__dirname, 'index.html'),
        // The /components gallery's iframe. A second document rather than a
        // route, so it renders one primitive without booting the app — and so it
        // owns a real viewport, which is the only way the app's
        // mobile `@media` rules (`constants/layout.js::MOBILE_QUERY`) can be reached from a desktop
        // browser. nginx serves it directly: `try_files $uri` finds the file
        // before the SPA fallback.
        canvas: path.resolve(__dirname, 'canvas.html'),
        // The rescue page, shown while the backend does not answer. A document
        // of its own for the same reason: it must boot nothing that needs the
        // backend. nginx serves it without one, and as the body of the
        // 502/503/504 it answers when the backend is not there.
        down: path.resolve(__dirname, 'milo-down.html'),
      },
    },
  },
  preview: {
    allowedHosts: ['milo.local']  // ✅ Autoriser milo.local
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        configure: (proxy, _options) => {
          proxy.on('error', (err, _req, _res) => {
            console.log('Erreur de proxy:', err);
          });
        },
      },
      '/librespot': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        configure: (proxy, _options) => {
          proxy.on('error', (err, _req, _res) => {
            console.log('Erreur de proxy librespot:', err);
          });
        },
      },
      '/roc': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        configure: (proxy, _options) => {
          proxy.on('error', (err, _req, _res) => {
            console.log('Erreur de proxy roc:', err);
          });
        },
      },
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
        configure: (proxy, _options) => {
          proxy.on('error', (err, _req, _res) => {
            console.log('Erreur de proxy WS:', err);
          });
        },
      },
      '/spotify': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        configure: (proxy, _options) => {
          proxy.on('error', (err, _req, _res) => {
            console.log('Erreur de proxy spotify:', err);
          });
        },
      }
    }
  }
}))