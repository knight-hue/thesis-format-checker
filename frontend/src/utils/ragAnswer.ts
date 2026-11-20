// RAG 三段式回答的展示处理。
// 契约来源（只读）：rag/answer.py::parse_answer 按【结论】/【规范依据】/【修改建议】切分，
// 但模型偶尔把依据、建议的正文也写进结论段，直接拼接会重复且难读。
// 这里按字段语义分段，再按行首标记二次归属，并跨段去重。

import type { AnswerSection, ChatResult } from '../types/chat'

const SECTION_ORDER = ['结论', '规范依据', '修改建议']
const SECTION_LINE_RE = /^[ \t]*【(结论|规范依据|修改建议)】[ \t]*/m
// 模型有时在正文里重复写出示例格式的标记行（如“【规范依据】”独占一行），单独处理。
const MARKER_ONLY_RE = /^[ \t]*【(?:结论|规范依据|修改建议)】[ \t]*$/
const MARKER_PREFIX_RE = /^[ \t]*【(?:结论|规范依据|修改建议)】[ \t]*/

function escapeHtml(text: string) {
  return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

// 单行 markdown 子集：先转义再套自己的标记，避免模型输出被当成标签执行。
function renderInline(text: string) {
  return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
}

// 纯标点或极短的无信息行（模型偶尔多写一个句号）直接丢弃。
function isNoiseLine(text: string) {
  return text.replace(/[\s。．.，,；;、：:！!？?—\-（）()【】]/g, '').length === 0
}

function toBlock(line: string) {
  const text = line.trim()
  const isListItem = /^(\d+[.、)]|[-•*])\s+/.test(text)
  const normalized = isListItem ? text.replace(/^[-•*]\s+/, '· ') : text
  return { html: renderInline(normalized), indent: isListItem }
}

// 同一条内容在多个段落里出现时只保留一次；依据/建议段优先占位，
// 结论段里重复的行随后被剔除，避免“【规范依据】”正文重复渲染。
function takeLines(chunks: string[], taken: Set<string>) {
  const blocks = []
  for (const chunk of chunks) {
    for (const rawLine of String(chunk).split('\n')) {
      const trimmed = rawLine.trim()
      if (!trimmed || isNoiseLine(trimmed) || MARKER_ONLY_RE.test(trimmed)) continue
      const line = trimmed.replace(MARKER_PREFIX_RE, '')
      if (!line || isNoiseLine(line)) continue
      const key = line.replace(/[\s。．.，,；;、：:]+$/, '')
      if (key && taken.has(key)) continue
      if (key) taken.add(key)
      blocks.push(toBlock(line))
    }
  }
  return blocks
}

export function buildAnswerSections(data?: Partial<Pick<ChatResult, 'conclusion' | 'basis' | 'suggestion'>>): AnswerSection[] {
  const buckets = new Map<string, string[]>(SECTION_ORDER.map((title) => [title, []]))
  const add = (title: string, text?: string) => {
    if (text) buckets.get(title)?.push(text)
  }

  // 结论段内若出现行首【规范依据】/【修改建议】，其后的正文归属到对应段。
  // 模型有时把标记接在句号后（“……。【规范依据】”），先补个换行再按行首切分。
  const conclusionRaw = String(data?.conclusion || '').replace(
    /([。；;])\s*(?=【(?:结论|规范依据|修改建议)】)/g,
    '$1\n'
  )
  const parts = conclusionRaw.split(SECTION_LINE_RE)
  add('结论', parts[0])
  for (let i = 1; i < parts.length; i += 2) {
    if (buckets.has(parts[i])) add(parts[i], parts[i + 1])
  }
  add('规范依据', data?.basis)
  add('修改建议', data?.suggestion)

  const taken = new Set<string>()
  const sections = []
  for (const title of ['规范依据', '修改建议']) {
    const blocks = takeLines(buckets.get(title) ?? [], taken)
    if (blocks.length) sections.push({ title, blocks })
  }
  const conclusionBlocks = takeLines(buckets.get('结论') ?? [], taken)
  if (conclusionBlocks.length) sections.unshift({ title: '结论', blocks: conclusionBlocks })

  // 只有一段时不显示标题，避免给单段回答硬套“结论”标签。
  if (sections.length === 1) sections[0].title = ''
  return sections
}
