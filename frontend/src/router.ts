import { createRouter, createWebHistory } from 'vue-router'
import ChatView from '@/views/ChatView.vue'
import KbDebugView from '@/views/KbDebugView.vue'

/**
 * 只做两个页面：对话页与知识库调试台。
 *
 * 刻意不做管理后台：那类 CRUD 界面看起来完整但技术含量低，
 * 而时间应该花在 Agent 的核心能力上。这个取舍写在这里，
 * 是为了让后来看到代码的人知道「不是漏了，是决定不做」。
 */
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'chat', component: ChatView, meta: { title: '对话' } },
    { path: '/kb', name: 'kb', component: KbDebugView, meta: { title: '知识库调试台' } },
  ],
})
