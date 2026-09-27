<script setup>
import { useRoute } from 'vue-router'
const route = useRoute()
</script>

<template>
  <div>
    <nav class="top-nav">
      <router-link to="/" class="nav-brand">Antigravity GPU Monitor</router-link>
      <div class="nav-links">
        <router-link to="/" :class="{ active: route.path === '/' }">Overview</router-link>
        <router-link to="/detail" :class="{ active: route.path === '/detail' }">Detail Panels</router-link>
      </div>
    </nav>
    <main class="main-content">
      <router-view />
    </main>
  </div>
</template>
