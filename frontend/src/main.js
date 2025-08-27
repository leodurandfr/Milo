import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { i18n } from './services/i18n'
import './assets/styles/reset.css'
import './assets/styles/design-system.css'

async function initApp() {
  const app = createApp(App)

  app.use(createPinia())
  app.use(router)

  // Configuration globale de l'i18n
  app.config.globalProperties.$t = i18n.t.bind(i18n)

  app.config.devtools = true

  // Initialiser la langue depuis le serveur avant de monter l'app
  await i18n.initializeLanguage()

  app.mount('#app')
}

initApp()