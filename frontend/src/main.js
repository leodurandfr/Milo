import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { i18n } from './services/i18n'
import { vPress } from './directives'
import './assets/styles/reset.css'
import './assets/styles/design-system.css'

async function initApp() {
  const app = createApp(App)

  app.use(createPinia())
  app.use(router)
  app.directive('press', vPress)

  app.config.globalProperties.$t = i18n.t.bind(i18n)

  app.config.devtools = true

  await i18n.initializeLanguage()

  app.mount('#app')
}

initApp()