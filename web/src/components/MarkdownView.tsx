/**
 * 轻量 Markdown 渲染：覆盖项目提示词 md 的典型结构，零依赖。
 *
 * 支持的语法（按优先级解析，够用不贪全）：
 * - 标题 `#` `##` `###`
 * - 引用块 `> `（连续多行合并）
 * - 围栏代码块 ``` （可选语言标注）
 * - 表格 `| a | b |`
 * - 无序/有序列表 `- ` `1. `
 * - 分隔线 `---`
 * - 行内：`**加粗**`、`` `行内代码` ``
 *
 * 为什么自己写而不是引 react-markdown：这个项目只渲染「提示词 md」这一种
 * 结构固定的文本，引重型解析器（unified/remark）得不偿失，还要在并行开发时
 * 冒依赖冲突的风险。这里按行做轻量状态机，输出 React 节点。
 */
import { type ReactNode } from "react"
import { cn } from "cn"

/** 行内加粗 + 行内代码。按 `**...**` 与 `` `...` `` 切分，逐段渲染。 */
function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = []
  // 同时匹配 `**加粗**` 与 `` `代码` ``，按出现顺序切分
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index))
    const token = m[0]
    if (token.startsWith("**")) {
      nodes.push(
        <strong key={i++} className="font-semibold text-paper">
          {token.slice(2, -2)}
        </strong>
      )
    } else {
      nodes.push(
        <code key={i++} className="rounded bg-black/40 px-1 font-mono text-[0.92em] text-[#c9c9c9]">
          {token.slice(1, -1)}
        </code>
      )
    }
    last = m.index + token.length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

/** 单块内容 → React 节点。块类型由前面扫描决定。 */
function renderBlock(kind: string, lines: string[], key: number): ReactNode {
  const join = lines.join("\n")
  switch (kind) {
    case "h1":
      return (
        <h1 key={key} className="mb-3 mt-5 text-[17px] font-semibold leading-snug text-paper first:mt-0">
          {renderInline(join.replace(/^#\s*/, ""))}
        </h1>
      )
    case "h2":
      return (
        <h2 key={key} className="mb-2 mt-4 text-[14px] font-semibold leading-snug text-paper">
          {renderInline(join.replace(/^##\s*/, ""))}
        </h2>
      )
    case "h3":
      return (
        <h3 key={key} className="mb-2 mt-3 text-[12.5px] font-semibold leading-snug text-paper">
          {renderInline(join.replace(/^###\s*/, ""))}
        </h3>
      )
    case "quote":
      return (
        <blockquote key={key} className="my-2 border-l-2 border-rule px-3 py-1 text-[12px] leading-relaxed text-ash">
          {lines.map((l, i) => (
            <p key={i} className={i > 0 ? "mt-1" : undefined}>
              {renderInline(l.replace(/^>\s?/, ""))}
            </p>
          ))}
        </blockquote>
      )
    case "code":
      return (
        <pre key={key} className="my-2 overflow-auto rounded-ctl border border-rule-soft bg-black/40 p-3">
          <code className="font-mono text-[11.5px] leading-relaxed text-[#c9c9c9]">{join}</code>
        </pre>
      )
    case "table":
      return <MarkdownTable key={key} lines={lines} />
    case "list":
      return (
        <ul key={key} className="my-2 flex flex-col gap-0.5 pl-4">
          {lines.map((l, i) => (
            <li key={i} className="list-disc text-[12px] leading-relaxed text-[#d7d7d7]">
              {renderInline(l.replace(/^[-*]\s+/, ""))}
            </li>
          ))}
        </ul>
      )
    case "olist":
      return (
        <ol key={key} className="my-2 flex flex-col gap-0.5 pl-4">
          {lines.map((l, i) => (
            <li key={i} className="list-decimal text-[12px] leading-relaxed text-[#d7d7d7]">
              {renderInline(l.replace(/^\d+[.)]\s+/, ""))}
            </li>
          ))}
        </ol>
      )
    case "hr":
      return <hr key={key} className="my-3 border-rule-soft" />
    default:
      return (
        <p key={key} className="my-1.5 text-[12.5px] leading-relaxed text-[#d7d7d7]">
          {renderInline(join)}
        </p>
      )
  }
}

/** 表格：首行是表头，第二行是 `---|---` 分隔。 */
function MarkdownTable({ lines }: { lines: string[] }) {
  const rows = lines
    .map((l) => l.trim().replace(/^\||\|$/g, ""))
    .filter(Boolean)
    .map((l) => l.split("|").map((c) => c.trim()))
  if (rows.length < 2) return null
  const head = rows[0]
  const body = rows.slice(1).filter((r) => !/^[-:\s]+$/.test(r.join("")))
  return (
    <div className="my-2 overflow-auto rounded-ctl border border-rule-soft">
      <table className="w-full border-collapse text-[11.5px]">
        <thead>
          <tr className="bg-cell/60">
            {head.map((c, i) => (
              <th key={i} className="border-b border-rule-soft px-2.5 py-1.5 text-left font-medium text-paper">
                {renderInline(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((r, ri) => (
            <tr key={ri} className="odd:bg-transparent even:bg-black/20">
              {head.map((_, ci) => (
                <td key={ci} className="border-b border-rule-soft/50 px-2.5 py-1.5 align-top text-[#d7d7d7]">
                  {renderInline(r[ci] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** 把整篇 md 文本切成块再渲染。 */
export function MarkdownView({ text, className }: { text: string; className?: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n")
  const blocks: ReactNode[] = []

  let i = 0
  let key = 0
  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    // 空行跳过
    if (!trimmed) {
      i += 1
      continue
    }

    // 代码块
    if (trimmed.startsWith("```")) {
      const buf: string[] = []
      i += 1
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        buf.push(lines[i])
        i += 1
      }
      i += 1 // 跳过结尾 ```
      blocks.push(renderBlock("code", buf, key++))
      continue
    }

    // 表格：当前行含 | 且下一行是分隔行
    if (trimmed.includes("|") && i + 1 < lines.length && /^\|?[\s:|-]+\|?$/.test(lines[i + 1].trim())) {
      const buf = [line]
      i += 1
      while (i < lines.length && lines[i].trim().includes("|")) {
        buf.push(lines[i])
        i += 1
      }
      blocks.push(renderBlock("table", buf, key++))
      continue
    }

    // 标题
    if (/^###\s/.test(trimmed)) {
      blocks.push(renderBlock("h3", [line], key++))
      i += 1
      continue
    }
    if (/^##\s/.test(trimmed)) {
      blocks.push(renderBlock("h2", [line], key++))
      i += 1
      continue
    }
    if (/^#\s/.test(trimmed)) {
      blocks.push(renderBlock("h1", [line], key++))
      i += 1
      continue
    }

    // 分隔线
    if (/^(-{3,}|\*{3,})\s*$/.test(trimmed)) {
      blocks.push(renderBlock("hr", [line], key++))
      i += 1
      continue
    }

    // 引用块
    if (trimmed.startsWith(">")) {
      const buf: string[] = []
      while (i < lines.length && lines[i].trim().startsWith(">")) {
        buf.push(lines[i])
        i += 1
      }
      blocks.push(renderBlock("quote", buf, key++))
      continue
    }

    // 无序列表
    if (/^[-*]\s+/.test(trimmed)) {
      const buf: string[] = []
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
        buf.push(lines[i])
        i += 1
      }
      blocks.push(renderBlock("list", buf, key++))
      continue
    }

    // 有序列表
    if (/^\d+[.)]\s+/.test(trimmed)) {
      const buf: string[] = []
      while (i < lines.length && /^\d+[.)]\s+/.test(lines[i].trim())) {
        buf.push(lines[i])
        i += 1
      }
      blocks.push(renderBlock("olist", buf, key++))
      continue
    }

    // 普通段落（连续非空行合并）
    const buf: string[] = [line]
    i += 1
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i].trim())) {
      buf.push(lines[i])
      i += 1
    }
    blocks.push(renderBlock("p", buf, key++))
  }

  return <div className={cn("px-5 py-4", className)}>{blocks}</div>
}

/** 判断一行是否是新块的起点（用于打断普通段落的连续合并）。 */
function isBlockStart(trimmed: string): boolean {
  return (
    trimmed.startsWith("```") ||
    trimmed.startsWith(">") ||
    /^#{1,3}\s/.test(trimmed) ||
    /^[-*]\s+/.test(trimmed) ||
    /^\d+[.)]\s+/.test(trimmed) ||
    /^(-{3,}|\*{3,})\s*$/.test(trimmed) ||
    (trimmed.includes("|") && false) // 表格在段落循环里单独处理，这里不打断
  )
}
