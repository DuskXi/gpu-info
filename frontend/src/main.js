import { createApp } from 'vue'
import './style.css'
import App from './App.vue'
import router from './router'
import RollingValue from './components/RollingValue.vue'

createApp(App).component('RollingValue', RollingValue).use(router).mount('#app')
