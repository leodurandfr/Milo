import { createApp } from 'vue'
import { createPinia } from 'pinia'
import axios from 'axios'
import App from './App.vue'
import router from './router'
import { i18n } from './services/i18n'
import { vPress } from './directives'
import './assets/styles/reset.css'
import './assets/styles/screen-corrections.css'
import './assets/styles/design-system.css'

// Rate-limited error reporter to backend (max 1 per second)
let lastErrorTime = 0
function reportError(source, error, info = '') {
  const now = Date.now()
  if (now - lastErrorTime < 1000) return
  lastErrorTime = now

  axios.post('/api/errors', {
    source,
    error: typeof error === 'string' ? error : (error?.message || String(error)),
    info: info || (error?.stack || '')
  }).catch(() => {}) // Never let reporting itself throw
}

// Capture uncaught JS errors
window.addEventListener('error', (event) => {
  reportError('window.onerror', event.message, `${event.filename}:${event.lineno}:${event.colno}`)
})

// Capture unhandled promise rejections
window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason
  reportError('unhandledrejection', reason)
})

async function initApp() {
  const app = createApp(App)

  app.use(createPinia())
  app.use(router)
  app.directive('press', vPress)

  app.config.globalProperties.$t = i18n.t.bind(i18n)

  app.config.devtools = true

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
