<script setup lang="ts">
/**
 * 知识库检索调试台。
 *
 * **这个页面存在的意义**：检索质量是 RAG 系统成败的关键，却也是最不透明的一环
 * ——出问题时你只看到「答案不对」，看不到「检索到了什么」。
 * 这里把混合检索的三条路径并列展示：
 *   BM25 单路（关键词） / 向量单路（语义） / RRF 融合（最终采用）
 * 于是「为什么混合比单路好」不再是论断，而是眼睛能看到的对比。
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import type { KbSearchResult } from '@/api/types'

const query = ref('多少度算高温')
const mode = ref<'auto' | 'bm25' | 'hybrid'>('auto')
const category = ref('')
const topK = ref(6)

const result = ref<KbSearchResult | null>(null)
const stats = ref<Record<string, any> | null>(null)
const loading = ref(false)
const error = ref('')

const CATEGORIES = [
  '',
  '术语',
  '灾害标准',
  '行业规则-能源',
  '行业规则-交通',
  '行业规则-农业',
  '数据说明',
]

const PRESETS = [
  '多少度算高温',
  '无人机能飞的最大风速是多少',
  '雷暴天气户外作业要注意什么',
  '光伏板什么时候该清洗',
  '预报为什么会不准',
]

onMounted(async () => {
  try {
    stats.value = await api.kbStats()
  } catch (e) {
    error.value = (e as Error).message
  }
  run()
})

async function run() {
  if (!query.value.trim() || loading.value) return
  loading.value = true
  error.value = ''
  try {
    result.value = await api.kbSearch({
      query: query.value.trim(),
      mode: mode.value,
      categories: category.value ? [category.value] : null,
      top_k: topK.value,
    })
  } catch (e) {
    error.value = (e as Error).message
    result.value = null
  } finally {
    loading.value = false
  }
}

function usePreset(q: string) {
  query.value = q
  run()
}

/** 归一化分数用于画条形图：两路分数量纲完全不同，各按自己的最大值归一 */
function barWidth(score: number, list?: Array<{ score: number }>): string {
  if (!list || !list.length) return '0%'
  const max = Math.max(...list.map((x) => x.score), 1e-9)
  return `${Math.max(4, (score / max) * 100)}%`
}

const fuseList = computed(() => result.value?.hits ?? [])
</script>

<template>
  <div class="kb">
    <!-- 顶部：知识库概览 -->
    <div class="stats card">
      <div class="stat">
        <div class="stat-num">{{ stats?.chunks ?? '—' }}</div>
        <div class="stat-label">知识块</div>
      </div>
      <div class="stat">
        <div class="stat-num">
          <span :class="stats?.vector_index ? 'ok' : 'warn'">
            {{ stats?.vector_index ? '可用' : '不可用' }}
          </span>
        </div>
        <div class="stat-label">向量索引</div>
      </div>
      <div class="stat wide">
        <div class="cats">
          <span v-for="(n, k) in stats?.categories || {}" :key="k" class="cat">
            {{ k }} <b>{{ n }}</b>
          </span>
        </div>
        <div class="stat-label">分类分布</div>
      </div>
    </div>

    <!-- 检索控制 -->
    <div class="panel card">
      <div class="controls">
        <input
          v-model="query"
          class="q-input"
          placeholder="输入检索问题，用自然语言即可"
          @keydown.enter="run"
        />
        <select v-model="mode">
          <option value="auto">自动（有向量走混合）</option>
          <option value="bm25">仅 BM25</option>
          <option value="hybrid">强制混合</option>
        </select>
        <select v-model="category">
          <option value="">全部分类</option>
          <option v-for="c in CATEGORIES.filter(Boolean)" :key="c" :value="c">{{ c }}</option>
        </select>
        <select v-model.number="topK">
          <option :value="3">top 3</option>
          <option :value="6">top 6</option>
          <option :value="10">top 10</option>
        </select>
        <button class="btn" :disabled="loading" @click="run">
          <i v-if="loading" class="spinner" /> 检索
        </button>
      </div>

      <div class="presets">
        <span class="muted">示例：</span>
        <button v-for="p in PRESETS" :key="p" class="preset" @click="usePreset(p)">{{ p }}</button>
      </div>
    </div>

    <div v-if="error" class="err card">⚠ {{ error }}</div>

    <!-- 三路对比 -->
    <div v-if="result" class="columns">
      <!-- BM25 -->
      <div class="col card">
        <div class="col-head">
          <span class="col-title">BM25 单路</span>
          <span class="col-sub">关键词精确匹配</span>
        </div>
        <div class="col-body">
          <div v-for="(h, i) in result.paths?.bm25 || []" :key="h.id" class="hit">
            <div class="hit-top">
              <span class="rank">{{ i + 1 }}</span>
              <span class="hit-id mono">{{ h.id }}</span>
              <span class="score mono">{{ h.score }}</span>
            </div>
            <div class="hit-text">{{ h.text }}</div>
            <div class="bar"><i :style="{ width: barWidth(h.score, result.paths?.bm25) }" /></div>
          </div>
          <p v-if="!result.paths?.bm25?.length" class="muted small">无结果</p>
        </div>
      </div>

      <!-- 向量 -->
      <div class="col card">
        <div class="col-head">
          <span class="col-title">向量单路</span>
          <span class="col-sub">语义相似（换说法也能找到）</span>
        </div>
        <div class="col-body">
          <div v-for="(h, i) in result.paths?.vector || []" :key="h.id" class="hit">
            <div class="hit-top">
              <span class="rank vec">{{ i + 1 }}</span>
              <span class="hit-id mono">{{ h.id }}</span>
              <span class="score mono">{{ h.score }}</span>
            </div>
            <div class="hit-text">{{ h.text }}</div>
            <div class="bar vec"><i :style="{ width: barWidth(h.score, result.paths?.vector) }" /></div>
          </div>
          <p v-if="!result.paths?.vector?.length" class="muted small">
            向量索引不可用，已降级为纯 BM25
          </p>
        </div>
      </div>

      <!-- 融合 -->
      <div class="col card fused">
        <div class="col-head">
          <span class="col-title">RRF 融合（最终采用）</span>
          <span class="col-sub">
            method={{ result.method }} · 候选 {{ result.total_candidates }}
          </span>
        </div>
        <div class="col-body">
          <div v-for="(h, i) in fuseList" :key="h.id || i" class="hit">
            <div class="hit-top">
              <span class="rank f">{{ i + 1 }}</span>
              <span class="badge badge--neutral">{{ h.category }}</span>
              <span class="hit-id mono">{{ h.id }}</span>
            </div>
            <div class="hit-text">{{ h.text }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.kb {
  height: 100%;
  overflow-y: auto;
  padding: 18px 22px 40px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* 概览 */
.stats {
  display: flex;
  gap: 28px;
  padding: 14px 18px;
  align-items: center;
  flex-wrap: wrap;
}
.stat-num {
  font-size: 19px;
  font-weight: 700;
  line-height: 1.2;
}
.stat-num .ok {
  color: var(--c-ok);
}
.stat-num .warn {
  color: var(--c-warn);
}
.stat-label {
  font-size: 11.5px;
  color: var(--c-text-3);
  margin-top: 2px;
}
.stat.wide {
  flex: 1;
  min-width: 240px;
}
.cats {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.cat {
  font-size: 11.5px;
  background: var(--c-surface-2);
  padding: 2px 8px;
  border-radius: 5px;
  color: var(--c-text-2);
}
.cat b {
  color: var(--c-primary-dark);
}

/* 控制条 */
.panel {
  padding: 14px 18px;
}
.controls {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.q-input {
  flex: 1;
  min-width: 220px;
  padding: 8px 12px;
  border: 1px solid var(--c-border-strong);
  border-radius: var(--r-sm);
  outline: none;
}
.q-input:focus {
  border-color: var(--c-primary);
  box-shadow: 0 0 0 3px var(--c-primary-soft);
}
select {
  padding: 8px 10px;
  border: 1px solid var(--c-border-strong);
  border-radius: var(--r-sm);
  background: var(--c-surface);
  outline: none;
}
.btn {
  padding: 8px 18px;
  background: var(--c-primary);
  color: #fff;
  font-weight: 600;
  border-radius: var(--r-sm);
}
.btn:hover:not(:disabled) {
  background: var(--c-primary-dark);
}
.presets {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
  margin-top: 10px;
  font-size: 12px;
}
.preset {
  background: var(--c-surface-2);
  border: 1px solid transparent;
  padding: 3px 10px;
  border-radius: 20px;
  font-size: 12px;
  color: var(--c-text-2);
}
.preset:hover {
  background: var(--c-primary-soft);
  color: var(--c-primary-dark);
}

.err {
  padding: 12px 16px;
  color: var(--c-err);
  background: var(--c-err-soft);
  border-color: var(--c-err-soft);
}

/* 三列 */
.columns {
  display: grid;
  grid-template-columns: 1fr 1fr 1.25fr;
  gap: 14px;
  align-items: start;
}
.col {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.col.fused {
  border-color: var(--c-primary);
  box-shadow: 0 0 0 3px var(--c-primary-soft);
}
.col-head {
  padding: 11px 14px;
  border-bottom: 1px solid var(--c-border);
  background: var(--c-surface-2);
}
.col-title {
  font-weight: 600;
  font-size: 13px;
  display: block;
}
.col-sub {
  font-size: 11px;
  color: var(--c-text-3);
}
.col-body {
  padding: 10px 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 60vh;
  overflow-y: auto;
}
.hit-top {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
}
.rank {
  width: 17px;
  height: 17px;
  border-radius: 4px;
  background: var(--c-warn-soft);
  color: var(--c-warn);
  font-size: 10.5px;
  font-weight: 700;
  display: grid;
  place-items: center;
  flex-shrink: 0;
}
.rank.vec {
  background: var(--c-accent-soft);
  color: var(--c-accent);
}
.rank.f {
  background: var(--c-primary-soft);
  color: var(--c-primary-dark);
}
.hit-id {
  color: var(--c-text-3);
  font-size: 10.5px;
}
.score {
  margin-left: auto;
  color: var(--c-text-3);
  font-size: 10.5px;
}
.hit-text {
  font-size: 12px;
  line-height: 1.6;
  color: var(--c-text-2);
  display: -webkit-box;
  -webkit-line-clamp: 4;
  line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.bar {
  height: 3px;
  background: var(--c-surface-2);
  border-radius: 2px;
  margin-top: 5px;
  overflow: hidden;
}
.bar i {
  display: block;
  height: 100%;
  background: var(--c-warn);
  border-radius: 2px;
}
.bar.vec i {
  background: var(--c-accent);
}
.small {
  font-size: 12px;
}

@media (max-width: 1100px) {
  .columns {
    grid-template-columns: 1fr;
  }
  .col-body {
    max-height: 40vh;
  }
}
</style>
