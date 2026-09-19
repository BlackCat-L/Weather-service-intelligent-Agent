/**
 * 极简 Markdown 渲染：只支持后端实际会输出的语法子集
 * （粗体、小标题、无序列表、有序列表、分隔线）。
 *
 * **为什么不引 marked / markdown-it**
 * 1. 答案由我们自己的模型生成，格式可控，完整解析器的能力用不上；
 * 2. 完整解析器 + 配套消毒库（DOMPurify）会显著增加包体；
 * 3. 解析器支持得越多，XSS 攻击面越大——用不到的能力就是纯风险。
 *
 * **安全前提**：必须先做 HTML 转义，再把 Markdown 标记替换成标签。
 * 顺序反了就等于把用户内容当 HTML 渲染，那才是真的漏洞。
 */

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** 行内语法：粗体、行内代码 */
function inline(s: string): string {
  return s
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
}

export function renderMarkdown(src: string): string {
  if (!src) return ''
  const text = escapeHtml(src)
  const out: string[] = []
  let listType: 'ul' | 'ol' | null = null

  const closeList = () => {
    if (listType) {
      out.push(`</${listType}>`)
      listType = null
    }
  }

  for (const raw of text.split('\n')) {
    const line = raw.trim()

    if (!line) {
      closeList()
      continue
    }

    // 分隔线
    if (/^-{3,}$/.test(line)) {
      closeList()
      out.push('<hr>')
      continue
    }

    // 无序列表（- 或 * 开头，也兼容模型有时输出的全角空格缩进）
    const ul = line.match(/^[-*]\s+(.*)$/)
    if (ul) {
      if (listType !== 'ul') {
        closeList()
        out.push('<ul>')
        listType = 'ul'
      }
      out.push(`<li>${inline(ul[1])}</li>`)
      continue
    }

    // 有序列表（1. 2. 这种）
    const ol = line.match(/^\d+[.、)]\s+(.*)$/)
    if (ol) {
      if (listType !== 'ol') {
        closeList()
        out.push('<ol>')
        listType = 'ol'
      }
      out.push(`<li>${inline(ol[1])}</li>`)
      continue
    }

    // 标题（### 及以下降级为加粗段落，避免超出卡片层级）
    const h = line.match(/^(#{1,6})\s+(.*)$/)
    if (h) {
      closeList()
      const level = Math.min(h[1].length + 2, 6)
      out.push(`<h${level}>${inline(h[2])}</h${level}>`)
      continue
    }

    closeList()
    out.push(`<p>${inline(line)}</p>`)
  }

  closeList()
  return out.join('\n')
}
