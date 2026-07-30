// frontend/src/canvas-main.js
/**
 * Entry point for the gallery's canvas iframe (canvas.html).
 *
 * Deliberately not main.js: mounting the app here would open a second
 * WebSocket, refetch settings and sit behind the boot gate, all to display one
 * button. This wires the four things a catalogued component actually needs —
 * design tokens, fonts, i18n and the `press` directive — and nothing else.
 * Pinia is created for the three store-coupled primitives (Dock, VolumeBar,
 * VirtualKeyboard), which read their stores' real defaults here rather than any
 * live state; the playground's `state` controls write into those same stores.
 */
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import CanvasApp from './components/gallery/CanvasApp.vue'
import { i18n } from './services/i18n'
import { vPress } from './directives'
import './assets/styles/reset.css'
import './assets/styles/design-system.css'

async function initCanvas() {
  const app = createApp(CanvasApp)

  app.use(createPinia())
  app.directive('press', vPress)
  app.config.globalProperties.$t = i18n.t.bind(i18n)

  await i18n.initializeLanguage()

  app.mount('#canvas')
}

initCanvas()
