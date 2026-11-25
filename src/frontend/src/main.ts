import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './style.css'
import App from './App.vue'
import { router } from './router'
import { pinia } from './stores/pinia'
import { registerUnauthorizedHandler } from './shared/api'

const app = createApp(App)

app.use(pinia)
app.use(router)
app.use(ElementPlus)

registerUnauthorizedHandler(() => {
  const current = router.currentRoute.value.fullPath
  if (!current.startsWith('/login')) {
    void router.replace({ path: '/login', query: { redirect: current } })
  }
})

app.mount('#app')
