<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useMonitorData } from './composables/useMonitorData'
import { time } from './format'

const route = useRoute()
const { snapshot, isConnected, isStale } = useMonitorData()
const status = computed(() => !isConnected.value ? '连接中断' : isStale.value ? '数据延迟' : '实时')
</script>

<template>
  <div class="shell">
    <header class="site-header">
      <router-link to="/" class="brand" aria-label="GPU 监视器首页">
        <span class="brand-mark">G<span>/</span></span>
        <span>GPU MONITOR</span>
      </router-link>
      <nav class="main-nav" aria-label="主导航">
        <router-link to="/" :class="{ active: route.path === '/' }">总览</router-link>
        <router-link to="/detail" :class="{ active: route.path === '/detail' }">趋势分析</router-link>
      </nav>
      <div class="connection" :class="{ offline: !isConnected || isStale }" role="status">
        <span class="status-dot"></span>
        <span>{{ status }}</span>
        <time class="update-time">{{ time(snapshot?.timestamp) }}</time>
      </div>
    </header>
    <main class="main-content"><router-view /></main>
    <footer class="site-footer">
      <span>设备 / 进程数据来源：{{ snapshot?.source || 'NVML' }}</span>
      <span>持续更新 · 最近 120 个采样</span>
    </footer>
  </div>
</template>
