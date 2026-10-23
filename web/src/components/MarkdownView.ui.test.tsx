// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { render } from "@testing-library/react"
import { MarkdownView } from "@/components/MarkdownView"

const SAMPLE = `# 标题一

> 引用行一
> 引用行二

## 小节

| 占位 | 文件名 |
| --- | --- |
| @人物1 | 08_云天娇.png |

\`\`\`
【风格】
16:9横屏
\`\`\`

- 列表项一
- 列表项二

1. 有序一
2. 有序二

**加粗文字** 和 \`行内代码\`

---

结尾段落
`

describe("MarkdownView", () => {
  it("渲染标题层级", () => {
    const { container } = render(<MarkdownView text={SAMPLE} />)
    expect(container.querySelector("h1")?.textContent).toBe("标题一")
    expect(container.querySelector("h2")?.textContent).toBe("小节")
  })

  it("渲染引用块", () => {
    const { container } = render(<MarkdownView text={SAMPLE} />)
    const quote = container.querySelector("blockquote")
    expect(quote).not.toBeNull()
    expect(quote?.textContent).toContain("引用行一")
  })

  it("渲染表格", () => {
    const { container } = render(<MarkdownView text={SAMPLE} />)
    const table = container.querySelector("table")
    expect(table).not.toBeNull()
    expect(table?.textContent).toContain("占位")
    expect(table?.textContent).toContain("@人物1")
  })

  it("渲染代码块", () => {
    const { container } = render(<MarkdownView text={SAMPLE} />)
    const pre = container.querySelector("pre")
    expect(pre).not.toBeNull()
    expect(pre?.textContent).toContain("【风格】")
  })

  it("渲染列表与行内样式", () => {
    const { container } = render(<MarkdownView text={SAMPLE} />)
    expect(container.querySelectorAll("ul li").length).toBe(2)
    expect(container.querySelectorAll("ol li").length).toBe(2)
    expect(container.querySelector("strong")?.textContent).toBe("加粗文字")
    expect(container.querySelector("p code")?.textContent).toBe("行内代码")
  })
})
