import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { i18n } from './services/i18n'
import { reportError } from './services/errorReporter'
import { vPress } from './directives'
import './assets/styles/reset.css'
import './assets/styles/design-system.css'

// Capture uncaught JS errors
window.addEventListener('error', (event) => {
  reportError('window.onerror', event.message, `${event.filename}:${event.lineno}:${event.colno}`)
})

// Capture unhandled promise rejections
window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason
  reportError('unhandledrejection', reason)
})

// A lazy chunk this bundle never loaded is gone once an in-app update swapped
// dist/ under an open tab: reload onto the served build, once per session so a
// chunk that is genuinely missing still reaches the reporter instead of looping.
// One import can fail several times (its CSS, then its JS) before the page
// unloads; those are swallowed too, or each would be reported.
const STALE_CHUNK_RELOAD_KEY = 'milo_stale_chunk_reload'
let reloadingForStaleChunk = false
window.addEventListener('vite:preloadError', (event) => {
  if (!reloadingForStaleChunk) {
    try {
      if (sessionStorage.getItem(STALE_CHUNK_RELOAD_KEY)) return
      sessionStorage.setItem(STALE_CHUNK_RELOAD_KEY, '1')
    } catch {
      return
    }
    reloadingForStaleChunk = true
    window.location.reload()
  }
  event.preventDefault()
})

async function initApp() {
  const app = createApp(App)

  app.use(createPinia())
  app.use(router)
  app.directive('press', vPress)

  // Capture Vue component errors (render, lifecycle, watchers)
  app.config.errorHandler = (err, instance, info) => {
    const component = instance?.$options?.name || instance?.$.type?.name || 'unknown'
    reportError(`vue:${component}`, err, info)
    console.error(`[Vue Error] ${component} - ${info}:`, err)
  }

  await i18n.initializeLanguage()

  app.mount('#app')
}

initApp()
