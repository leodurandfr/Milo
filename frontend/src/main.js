import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { i18n } from './services/i18n'
import './assets/styles/reset.css'
import './assets/styles/design-system.css'

// Press animation: ensures minimum 150ms visible feedback on rapid clicks
document.addEventListener('pointerdown', (e) => {
  const el = e.target.closest('.interactive-press, .interactive-press-strong');
  if (!el || el.disabled) return;

  el.classList.add('pressed');
  setTimeout(() => el.classList.remove('pressed'), 150);
}, { passive: true });

async function initApp() {
  const app = createApp(App)

  app.use(createPinia())
  app.use(router)

  app.config.globalProperties.$t = i18n.t.bind(i18n)

  app.config.devtools = true

  await i18n.initializeLanguage()

  app.mount('#app')
}

initApp()