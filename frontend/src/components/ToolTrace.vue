<script setup lang="ts">
import { computed, ref } from 'vue'
import type { TraceEntry } from '@/api/types'
import { toolMeta } from '@/utils/tools'

const props = defineProps<{ trace: TraceEntry[]; live?: boolean }>()

const expanded = ref<Set<number>>(new Set())

function toggle(i: number) {
  const s = new Set(expanded.value)
  s.has(i) ? s.delete(i) : s.add(i)
  expanded.value = s
}

/** 参数摘要：把入参压成一行，太长就省略 */
function argsSummary(args: Record<string, unknown>): string {
  if (!args || !Object.keys(args).length) return ''
  const parts = Object.entries(args).map(([k, v]) => {
    const val = typeof v === 'string' ? v : JSON.stringify(v)
    return `${k}=${val}`
  })
  const s = parts.join('  ')
  return s.length > 74 ? s.slice(0, 74) + '…' : s
}

function isError(entry: TraceEntry): boolean {
  const r = entry.result as Record<string, unknown> | null
  return !!r && typeof r === 'object' && 'error' in r
}

function pretty(obj: unknown): string {
  try {
    return JSON.stringify(obj, null, 2)
  } catch {
    return String(obj)
  }
}

const items = computed(() => props.trace)
</script>

<template>
  <div class="trace">
    <div v-if="!items.length" class="empty muted">
      {{ live ? '等待模型决策…' : '本轮没有调用工具' }}
    </div>

    <div
      v-for="(t, i) in items"
      :key="i"
      class="item fade-up"
      :class="{ pending: t.result === null, error: isError(t) }"
    >
      <div class="rail">
        <div class="dot">{{ toolMeta(t.name).icon }}</div>
        <div v-if="i < items.length - 1" class="line" />
      </div>

      <div class="content">
        <div class="head" @click="toggle(i)">
          <span class="label">{{ toolMeta(t.name).label }}</span>
          <span class="fname mono">{{ t.name }}</span>
          <span class="round badge badge--neutral">第 {{ t.round }} 轮</span>

          <span v-if="t.result === null" class="status badge badge--warn">
            <i class="spinner" /> 执行中
          </span>
          <span v-else-if="isError(t)" class="status badge badge--err">失败</span>
          <span v-else class="status badge badge--ok">完成</span>

          <span class="chev">{{ expanded.has(i) ? '收起' : '详情' }}</span>
        </div>

        <!-- 参数是「自主决策」最硬的证据：这些值不是代码写死的，
             而是模型自己从用户问题里提取、自己算出来的
             （比如从「余杭区明天…」里提取出 place=余杭区、换算出 date=2026-09-20） -->
        <div v-if="argsSummary(t.args)" class="args">
          <span class="args-label">模型传入</span>
          <span class="mono">{{ argsSummary(t.args) }}</span>
        </div>

        <pre v-if="expanded.has(i) && t.result" class="raw">{{
          pretty(t.result)
        }}</pre>
      </div>
    </div>
  </div>
</template>

<style scoped>
.trace {
  display: flex;
  flex-direction: column;
}
.empty {
  font-size: 12.5px;
  padding: 8px 2px;
}

.item {
  display: flex;
  gap: 10px;
}

.rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 0 0 26px;
}

.dot {
  width: 26px;
  height: 26px;
  border-radius: 8px;
  background: var(--c-tool-soft);
  display: grid;
  place-items: center;
  font-size: 13px;
  flex-shrink: 0;
}

.item.pending .dot {
  background: var(--c-warn-soft);
}

.item.error .dot {
  background: var(--c-err-soft);
}

.line {
  flex: 1;
  width: 2px;
  background: var(--c-border);
  margin: 3px 0;
  min-height: 10px;
}

.content {
  flex: 1;
  min-width: 0;
  padding-bottom: 12px;
}

.head {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  cursor: pointer;
  padding: 3px 0;
}

.head:hover .label {
  color: var(--c-primary);
}

.label {
  font-weight: 600;
  font-size: 13px;
}

.fname {
  color: var(--c-text-3);
}

.round,
.status {
  font-size: 11px;
  height: 19px;
}

.chev {
  margin-left: auto;
  font-size: 11.5px;
  color: var(--c-accent);
}

.args {
  color: var(--c-text-2);
  word-break: break-all;
  margin-top: 3px;
  line-height: 1.5;
  display: flex;
  gap: 6px;
  align-items: baseline;
}

.args-label {
  flex-shrink: 0;
  font-size: 10.5px;
  color: var(--c-accent);
  background: var(--c-accent-soft);
  padding: 1px 5px;
  border-radius: 3px;
}

.raw {
  margin: 8px 0 0;
  padding: 10px;
  background: var(--c-surface-2);
  border-radius: var(--r-sm);
  max-height: 260px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--c-text-2);
  line-height: 1.5;
}
</style>
