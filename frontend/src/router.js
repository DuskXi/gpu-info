import { createRouter, createWebHashHistory } from 'vue-router'
import Overview from './views/Overview.vue'
import Detail from './views/Detail.vue'

const routes = [
  { path: '/', name: 'overview', component: Overview },
  { path: '/detail', name: 'detail', component: Detail },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
