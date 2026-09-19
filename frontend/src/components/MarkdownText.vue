<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '@/utils/markdown'

const props = defineProps<{ text: string }>()

// renderMarkdown 内部已做 HTML 转义，v-html 在这里是安全的：
// 转义在前、替换在后，注入的标签只可能来自我们自己的替换规则。
const html = computed(() => renderMarkdown(props.text))
</script>

<template>
  <div class="md" v-html="html" />
</template>

<style scoped>
.md :deep(p) {
  margin: 0 0 8px;
}
.md :deep(p:last-child) {
  margin-bottom: 0;
}
.md :deep(strong) {
  color: var(--c-primary-dark);
  font-weight: 600;
}
.md :deep(h3),
.md :deep(h4),
.md :deep(h5) {
  margin: 12px 0 6px;
  font-size: 14px;
}
.md :deep(ul),
.md :deep(ol) {
  margin: 6px 0 10px;
  padding-left: 20px;
}
.md :deep(li) {
  margin-bottom: 3px;
}
.md :deep(code) {
  background: var(--c-surface-2);
  padding: 1px 5px;
  border-radius: 4px;
  color: var(--c-tool);
}
.md :deep(hr) {
  border: 0;
  border-top: 1px solid var(--c-border);
  margin: 12px 0;
}
</style>
