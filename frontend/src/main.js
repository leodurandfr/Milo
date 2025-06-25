import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './assets/styles/reset.css'
import './assets/styles/design-system.css'

const app = createApp(App)

app.use(createPinia())
app.use(router)

app.config.devtools = true


app.mount('#app')