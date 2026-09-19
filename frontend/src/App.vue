<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { api } from '@/api/client'
import type { HealthInfo } from '@/api/types'

const route = useRoute()
const health = ref<HealthInfo | null>(null)
const healthError = ref('')

onMounted(async () => {
  try {
    health.value = await api.health()
  } catch (e) {
    healthError.value = (e as Error).message
  }
})
</script>

<template>
  <div class="shell">
    <header class="topbar">
      <div class="brand">
        <div class="logo">气</div>
        <div>
          <h1>气象服务智能体</h1>
          <p class="muted">面向能源 / 交通 / 农业的气象决策支持</p>
        </div>
      </div>

      <nav class="nav">
        <RouterLink to="/" :class="{ active: route.name === 'chat' }">对话</RouterLink>
        <RouterLink to="/kb" :class="{ active: route.name === 'kb' }">知识库调试台</RouterLink>
        <a href="/docs" target="_blank" rel="noopener">API 文档</a>
      </nav>

      <div class="status">
        <template v-if="health">
          <span class="badge badge--primary">{{ health.llm_provider }} / {{ health.llm_model }}</span>
          <span
            class="badge"
            :class="health.knowledge_base.vector_index ? 'badge--ok' : 'badge--warn'"
            :title="health.knowledge_base.vector_index ? '向量索引可用，混合检索生效' : '向量索引不可用，已降级为纯 BM25'"
          >
            知识库 {{ health.knowledge_base.chunks ?? 0 }} 块
            {{ health.knowledge_base.vector_index ? '· 混合检索' : '· 仅 BM25' }}
          </span>
          <span class="badge badge--neutral">{{ health.tools_count }} 个工具</span>
        </template>
        <span v-else-if="healthError" class="badge badge--err" :title="healthError">
          后端未连接
        </span>
        <span v-else class="badge badge--neutral"><i class="spinner"></i> 连接中</span>
      </div>
    </header>

    <main class="body">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.topbar {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 0 20px;
  height: 60px;
  background: var(--c-surface);
  border-bottom: 1px solid var(--c-border);
  flex-shrink: 0;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
}

.logo {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: linear-gradient(135deg, var(--c-primary), #14b8a6);
  color: #fff;
  font-weight: 700;
  display: grid;
  place-items: center;
  font-size: 16px;
}

.brand h1 {
  font-size: 15px;
}

.brand p {
  margin: 0;
  font-size: 11.5px;
}

.nav {
  display: flex;
  gap: 4px;
  margin-left: 8px;
}

.nav a {
  padding: 6px 12px;
  border-radius: var(--r-sm);
  color: var(--c-text-2);
  text-decoration: none;
  font-size: 13px;
  font-weight: 500;
}

.nav a:hover {
  background: var(--c-surface-2);
  color: var(--c-text);
}

.nav a.active {
  background: var(--c-primary-soft);
  color: var(--c-primary-dark);
}

.status {
  margin-left: auto;
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.body {
  flex: 1;
  min-height: 0;
}
</style>
