import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { i18n } from './services/i18n'
import { apiCall } from './services/apiCall'
import { vPress } from './directives'
import './assets/styles/reset.css'
import './assets/styles/screen-corrections.css'
import './assets/styles/design-system.css'

// Rate-limited error reporter to backend (max 1 per second).
// Uses apiCall.post with logLevel: 'debug' so a failed report never floods the
// console (it would be a debug message, not an error — preventing recursion
// even though logger.{error,debug} only write to console).
let lastErrorTime = 0
function reportError(source, error, info = '') {
  const now = Date.now()
  if (now - lastErrorTime < 1000) return
  lastErrorTime = now

  apiCall.post('/api/errors', {
    source,
    error: typeof error === 'string' ? error : (error?.message || String(error)),
    info: info || (error?.stack || ''),
  }, {
    category: 'reporter',
    message: 'Error reporter POST failed',
    logLevel: 'debug',
  })
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
