/**
 * 对话状态管理。
 *
 * 把「发起提问 → 消费 SSE 事件 → 更新界面状态」这套流程收拢在一处，
 * 组件只负责渲染。这样做的直接好处：新增一种事件类型时只改这个文件，
 * 不用在多个组件里各改一遍。
 */
import { defineStore } from 'pinia'
import { computed, reactive, ref } from 'vue'
import { streamChat } from '@/api/client'
import type { ChatMode, Turn, Verification } from '@/api/types'

let seq = 0
const newId = () => `t${Date.now()}_${seq++}`

export const useConversation = defineStore('conversation', () => {
  const turns = ref<Turn[]>([])
  const mode = ref<ChatMode>('single')
  const sessionId = ref<string | null>(null)

  /** 当前正在执行的那一轮（用于右侧实时面板） */
  const activeId = ref<string | null>(null)
  let abortFn: (() => void) | null = null

  const activeTurn = computed(() =>
    turns.value.find((t) => t.id === activeId.value) || turns.value[turns.value.length - 1] || null,
  )
  const running = computed(() => !!activeTurn.value?.running)

  function ask(question: string) {
    const q = question.trim()
    if (!q || running.value) return

    // ⚠️ 必须用 reactive() 创建，不能是普通对象。
    //
    // 踩过的坑：一开始写的是 `const turn: Turn = {...}` 然后 push 进 ref 数组。
    // Vue 的响应式数组在读取时才把元素包成代理，而我闭包里持有的始终是**原始对象**
    // ——后续 `turn.answer = ...` 改的是原始对象，代理的 set 陷阱不会被触发，
    // 于是数据确实变了、界面却一动不动（表现为「一直转圈，最后也不出结果」，
    // 但后端日志显示请求 200 正常完成，排查时极易误判为后端问题）。
    //
    // 用 reactive() 从一开始就持有代理，后续所有赋值都能正确触发更新。
    const turn = reactive<Turn>({
      id: newId(),
      question: q,
      mode: mode.value,
      answer: '',
      trace: [],
      verification: null,
      rounds: 0,
      sessionId: sessionId.value,
      stage: '',
      running: true,
      error: null,
      startedAt: Date.now(),
      elapsedMs: null,
    })
    turns.value.push(turn)
    activeId.value = turn.id

    abortFn = streamChat(
      { question: q, session_id: sessionId.value, mode: mode.value },
      {
        onStart: (d) => {
          turn.mode = (d.mode as ChatMode) || turn.mode
        },
        onStage: (d) => {
          turn.stage = d.stage
        },
        onStageDone: () => {
          turn.stage = ''
        },
        onToolCalls: (d) => {
          // 先把「正在调用」写进轨迹，用户能立刻看到 Agent 在干活，
          // 而不是干等一个转圈图标（这一步对体感速度的影响很大）
          for (const c of d.calls) {
            let parsed: Record<string, unknown> = {}
            try {
              parsed = JSON.parse(c.args || '{}')
            } catch {
              parsed = { _raw: c.args }
            }
            turn.trace.push({ round: d.round, name: c.name, args: parsed, result: null })
          }
        },
        onToolResult: (d) => {
          // 按「轮次 + 工具名 + 尚未回填」匹配到对应的占位记录
          const item = [...turn.trace]
            .reverse()
            .find((t) => t.round === d.round && t.name === d.name && t.result === null)
          if (item) item.result = d.result
        },
        onVerification: (v) => {
          turn.verification = v as unknown as Verification
        },
        onDone: (d) => {
          turn.answer = d.answer
          turn.rounds = d.rounds
          turn.sessionId = d.session_id
          sessionId.value = d.session_id
          turn.running = false
          turn.stage = ''
          turn.elapsedMs = Date.now() - turn.startedAt
          abortFn = null
        },
        onError: (msg) => {
          turn.error = msg
          turn.running = false
          turn.stage = ''
          turn.elapsedMs = Date.now() - turn.startedAt
          abortFn = null
        },
      },
    )
  }

  function cancel() {
    abortFn?.()
    abortFn = null
  }

  function reset() {
    cancel()
    turns.value = []
    sessionId.value = null
    activeId.value = null
  }

  return { turns, mode, sessionId, activeTurn, running, ask, cancel, reset }
})
