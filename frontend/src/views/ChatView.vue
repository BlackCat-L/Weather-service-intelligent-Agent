<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import ChartPanel from '@/components/ChartPanel.vue'
import MarkdownText from '@/components/MarkdownText.vue'
import ToolTrace from '@/components/ToolTrace.vue'
import VerificationPanel from '@/components/VerificationPanel.vue'
import { useConversation } from '@/stores/conversation'

const store = useConversation()
const input = ref('')
const listEl = ref<HTMLElement | null>(null)

const EXAMPLES = [
  '余杭区明天适合安排光伏板清洗吗？',
  '内蒙古乌兰察布明天风电出力怎么样？',
  '杭州未来三天适合作户外作业吗？',
  '光伏组件的温度和气温一般差多少？',
]

/** 右侧面板展示的轮次：优先正在执行的那一轮，否则最后一轮 */
const panelTurn = computed(() => store.activeTurn)

/** 用户滚上去看历史时不要强制拽回底部——这是流式界面最容易惹人烦的细节 */
let follow = true
function onScroll() {
  const el = listEl.value
  if (!el) return
  follow = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

watch(
  () => store.turns.map((t) => `${t.answer.length}|${t.trace.length}|${t.running}`).join(','),
  async () => {
    if (!follow) return
    await nextTick()
    const el = listEl.value
    if (el) el.scrollTop = el.scrollHeight
  },
)

function send() {
  const q = input.value.trim()
  if (!q || store.running) return
  store.ask(q)
  input.value = ''
  follow = true
}

function useExample(q: string) {
  input.value = q
  send()
}

function onKeydown(e: KeyboardEvent) {
  // Enter 发送，Shift+Enter 换行——聊天框的通用约定
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}
</script>

<template>
  <div class="chat">
    <!-- 左：对话 -->
    <section class="col-left">
      <div ref="listEl" class="messages" @scroll="onScroll">
        <div v-if="!store.turns.length" class="welcome">
          <div class="logo-big">气</div>
          <h2>气象服务智能体</h2>
          <p class="muted">
            直接提问即可。Agent 会自主决定调用哪些工具——
            查地名坐标、拉气象数据、算发电出力、检索行业规则——
            并给出可执行的结论。
          </p>
          <div class="examples">
            <button v-for="q in EXAMPLES" :key="q" class="example" @click="useExample(q)">
              {{ q }}
            </button>
          </div>
        </div>

        <div v-for="t in store.turns" :key="t.id" class="turn">
          <div class="q">
            <span class="q-tag">问</span>
            <span>{{ t.question }}</span>
          </div>

          <div class="a card">
            <div class="a-head">
              <span class="badge badge--primary">
                {{ t.mode === 'multi' ? '多智能体协同' : '单 Agent' }}
              </span>
              <span v-if="t.elapsedMs" class="badge badge--neutral">
                {{ (t.elapsedMs / 1000).toFixed(1) }}s
              </span>
              <span v-if="t.trace.length" class="badge badge--tool">
                {{ t.trace.length }} 次工具调用
              </span>
              <span
                v-if="t.verification"
                class="badge"
                :class="t.verification.verdict === 'pass' ? 'badge--ok' : 'badge--warn'"
              >
                归因
                {{
                  t.verification.grounded_rate !== null
                    ? Math.round(t.verification.grounded_rate * 100) + '%'
                    : '—'
                }}
              </span>
            </div>

            <div v-if="t.stage" class="stage">
              <i class="spinner" /> {{ t.stage }}
            </div>

            <div v-if="t.error" class="err">⚠ {{ t.error }}</div>

            <MarkdownText v-else-if="t.answer" :text="t.answer" />

            <div v-else-if="t.running" class="thinking muted">
              <i class="spinner" /> 正在调用工具并分析…
            </div>
          </div>

          <!-- 该轮的工具轨迹折叠在对话里，便于回看 -->
          <details v-if="t.trace.length && !t.running" class="history-trace">
            <summary>查看这一轮的工具调用过程</summary>
            <ToolTrace :trace="t.trace" />
          </details>
        </div>
      </div>

      <div class="composer">
        <div class="composer-row">
          <div class="modes">
            <button
              :class="['mode', { on: store.mode === 'single' }]"
              @click="store.mode = 'single'"
              title="一个 Agent 循环调工具，速度快"
            >
              单 Agent
            </button>
            <button
              :class="['mode', { on: store.mode === 'multi' }]"
              @click="store.mode = 'multi'"
              title="数据/知识并行检索 → 交叉分析 → 汇总，更全面但更慢"
            >
              多智能体协同
            </button>
          </div>
          <button class="link" @click="store.reset()" :disabled="store.running">清空对话</button>
        </div>

        <div class="input-row">
          <textarea
            v-model="input"
            rows="2"
            placeholder="问一个气象决策问题，例如：余杭区明天适合清洗光伏板吗？（Enter 发送，Shift+Enter 换行）"
            @keydown="onKeydown"
          />
          <button v-if="store.running" class="btn btn-ghost" @click="store.cancel()">停止</button>
          <button v-else class="btn btn-primary" :disabled="!input.trim()" @click="send">发送</button>
        </div>
      </div>
    </section>

    <!-- 右：实时执行面板 -->
    <aside class="col-right">
      <div class="panel-head">
        <h3>执行过程</h3>
        <span v-if="panelTurn" class="muted mono">{{ panelTurn.id.slice(-6) }}</span>
      </div>

      <div class="panel-body">
        <template v-if="panelTurn">
          <div class="block">
            <div class="block-title">
              工具调用轨迹
              <span v-if="panelTurn.running" class="badge badge--warn"><i class="spinner" /> 进行中</span>
            </div>
            <ToolTrace :trace="panelTurn.trace" :live="panelTurn.running" />
          </div>

          <ChartPanel v-if="panelTurn.trace.length" :trace="panelTurn.trace" />

          <div class="block">
            <VerificationPanel :verification="panelTurn.verification" />
          </div>
        </template>

        <div v-else class="panel-empty muted">
          提问后，这里会实时显示 Agent 的工具调用、绘制出力曲线，并给出数值归因校验结果。
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.chat {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 380px;
  height: 100%;
  min-height: 0;
}

/* ---------- 左列 ---------- */
.col-left {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 22px 24px 8px;
  min-height: 0;
}

.welcome {
  max-width: 620px;
  margin: 40px auto 0;
  text-align: center;
}

.logo-big {
  width: 54px;
  height: 54px;
  margin: 0 auto 14px;
  border-radius: 14px;
  background: linear-gradient(135deg, var(--c-primary), #14b8a6);
  color: #fff;
  font-size: 26px;
  font-weight: 700;
  display: grid;
  place-items: center;
}

.welcome h2 {
  font-size: 19px;
  margin-bottom: 8px;
}

.welcome p {
  margin: 0 auto 20px;
  max-width: 480px;
  line-height: 1.75;
}

.examples {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: stretch;
}

.example {
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
  padding: 10px 14px;
  text-align: left;
  color: var(--c-text-2);
  font-size: 13px;
}

.example:hover {
  border-color: var(--c-primary);
  color: var(--c-primary-dark);
  background: var(--c-primary-soft);
}

.turn {
  margin-bottom: 22px;
}

.q {
  display: flex;
  gap: 9px;
  align-items: flex-start;
  margin-bottom: 9px;
  font-weight: 500;
}

.q-tag {
  flex: 0 0 22px;
  height: 22px;
  border-radius: 6px;
  background: var(--c-accent-soft);
  color: var(--c-accent);
  font-size: 12px;
  font-weight: 700;
  display: grid;
  place-items: center;
}

.a {
  padding: 14px 16px;
  margin-left: 31px;
}

.a-head {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.stage {
  font-size: 12.5px;
  color: var(--c-warn);
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 5px 0;
}

.thinking {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.err {
  color: var(--c-err);
  background: var(--c-err-soft);
  padding: 9px 12px;
  border-radius: var(--r-sm);
  font-size: 13px;
}

.history-trace {
  margin: 8px 0 0 31px;
  font-size: 12.5px;
  color: var(--c-text-2);
}

.history-trace summary {
  cursor: pointer;
  padding: 5px 0;
  color: var(--c-accent);
}

.history-trace summary:hover {
  color: var(--c-primary-dark);
}

/* ---------- 输入区 ---------- */
.composer {
  border-top: 1px solid var(--c-border);
  background: var(--c-surface);
  padding: 10px 24px 14px;
  flex-shrink: 0;
}

.composer-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.modes {
  display: flex;
  gap: 4px;
  background: var(--c-surface-2);
  padding: 3px;
  border-radius: var(--r-sm);
}

.mode {
  background: transparent;
  padding: 4px 11px;
  font-size: 12.5px;
  color: var(--c-text-2);
  border-radius: 5px;
}

.mode.on {
  background: var(--c-surface);
  color: var(--c-primary-dark);
  font-weight: 600;
  box-shadow: var(--shadow-sm);
}

.link {
  background: none;
  color: var(--c-text-3);
  font-size: 12.5px;
}
.link:hover {
  color: var(--c-err);
}

.input-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

textarea {
  flex: 1;
  resize: none;
  padding: 9px 12px;
  border: 1px solid var(--c-border-strong);
  border-radius: var(--r-md);
  outline: none;
  line-height: 1.6;
  background: var(--c-surface);
}

textarea:focus {
  border-color: var(--c-primary);
  box-shadow: 0 0 0 3px var(--c-primary-soft);
}

.btn {
  padding: 9px 20px;
  border-radius: var(--r-md);
  font-weight: 600;
  font-size: 13px;
  flex-shrink: 0;
}

.btn-primary {
  background: var(--c-primary);
  color: #fff;
}
.btn-primary:hover:not(:disabled) {
  background: var(--c-primary-dark);
}

.btn-ghost {
  background: var(--c-surface);
  border-color: var(--c-border-strong);
  color: var(--c-text-2);
}
.btn-ghost:hover {
  border-color: var(--c-err);
  color: var(--c-err);
}

/* ---------- 右列 ---------- */
.col-right {
  border-left: 1px solid var(--c-border);
  background: var(--c-surface);
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--c-border);
  flex-shrink: 0;
}

.panel-head h3 {
  font-size: 13.5px;
}

.panel-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 16px 24px;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.block-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--c-text-2);
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 7px;
}

.panel-empty {
  font-size: 12.5px;
  line-height: 1.75;
  padding-top: 20px;
}

@media (max-width: 1080px) {
  .chat {
    grid-template-columns: 1fr;
    grid-template-rows: minmax(0, 1fr) minmax(0, 1fr);
  }
  .col-right {
    border-left: 0;
    border-top: 1px solid var(--c-border);
  }
}
</style>
