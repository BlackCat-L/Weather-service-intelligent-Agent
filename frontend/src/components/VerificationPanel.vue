<script setup lang="ts">
import { computed } from 'vue'
import type { Verification } from '@/api/types'

const props = defineProps<{ verification: Verification | null }>()

const rate = computed(() => {
  const r = props.verification?.grounded_rate
  return r === null || r === undefined ? null : Math.round(r * 100)
})

const tone = computed(() => {
  if (!props.verification) return 'neutral'
  return props.verification.verdict === 'pass' ? 'ok' : 'warn'
})

const dateIssues = computed(() => props.verification?.dates?.ungrounded_dates ?? [])
</script>

<template>
  <div class="verify card">
    <div class="head">
      <span class="title">数值归因校验</span>
      <span class="badge" :class="`badge--${tone}`">
        <template v-if="rate !== null">{{ rate }}% 可追溯</template>
        <template v-else>无法校验</template>
      </span>
    </div>

    <p class="explain muted">
      检查回答里的每个数字与日期，能否在工具返回结果里找到出处——防止模型编造气象数值。
    </p>

    <div v-if="verification" class="body">
      <div class="row">
        <span class="k">数值</span>
        <span class="v">
          <strong>{{ verification.grounded }}</strong> / {{ verification.total }} 个有出处
        </span>
      </div>

      <div v-if="verification.ungrounded.length" class="row">
        <span class="k warn">未归因数值</span>
        <span class="v">
          <code v-for="n in verification.ungrounded" :key="n" class="chip">{{ n }}</code>
        </span>
      </div>

      <div v-if="dateIssues.length" class="row">
        <span class="k warn">无依据日期</span>
        <span class="v">
          <code v-for="d in dateIssues" :key="d" class="chip">{{ d }}</code>
        </span>
      </div>

      <p v-if="verification.note" class="note muted">{{ verification.note }}</p>
    </div>

    <p v-else class="muted">本轮尚未产生校验结果。</p>
  </div>
</template>

<style scoped>
.verify {
  padding: 12px 14px;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.title {
  font-weight: 600;
  font-size: 13px;
}
.explain {
  margin: 0 0 10px;
  font-size: 11.5px;
  line-height: 1.5;
}
.body {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.row {
  display: flex;
  gap: 8px;
  align-items: baseline;
  font-size: 12.5px;
}
.k {
  flex: 0 0 76px;
  color: var(--c-text-2);
}
.k.warn {
  color: var(--c-warn);
}
.v {
  flex: 1;
  min-width: 0;
}
.chip {
  display: inline-block;
  background: var(--c-warn-soft);
  color: var(--c-warn);
  padding: 0 6px;
  border-radius: 4px;
  margin-right: 4px;
  font-size: 11.5px;
}
.note {
  margin: 4px 0 0;
  font-size: 11.5px;
}
</style>
