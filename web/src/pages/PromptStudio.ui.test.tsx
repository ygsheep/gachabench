// @vitest-environment jsdom
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { Rendered, SegmentDetail, SegmentItem } from "@/lib/promptsApi"
import { checkPrompt, listFiles, setBinding } from "@/lib/promptsApi"

/* 三栏各自出什么：
   左 = 段列表，中 = 21 字段表 / 素材，右 = 三段式代码块。
   这里不测样式，只钉住「哪一栏该出现什么」，防止以后改布局把某一栏整块弄丢。 */

const SEGMENTS: SegmentItem[] = [
  {
    id: "55-4",
    ep: 55,
    no: 4,
    title: "第55集 第4段 分镜脚本（60–90s）｜30s / 12 镜",
    source_path: "分镜/第55集_第4段_分镜脚本_60-90s.md",
    range: { start: 60, end: 90 },
    duration: 30,
    shots: 2,
    spoken: 1,
    status: "READY",
    issues: 0,
    prompt_file: "",
  },
  {
    id: "55-5",
    ep: 55,
    no: 5,
    title: "第55集 第5段 分镜脚本（90–120s）",
    source_path: "分镜/第55集_第5段_分镜脚本_90-120s.md",
    range: { start: 90, end: 120 },
    duration: 30,
    shots: 15,
    spoken: 0,
    status: "PARTIAL",
    issues: 1,
    prompt_file: "05_30_01_090-120s_参考体例版.md",
  },
]

const SHOT = {
  no: "SC01-SH025",
  seq: 25,
  scene: "SC01 云家西屋·内·夜",
  framing: "MCU",
  camera: "俯拍",
  position: "侧位",
  movement: "固定",
  focal: "长焦",
  duration: 2.4,
  start: 0,
  end: 2.4,
  content: "他侧躺在帐里，右手从被子边缘探进去",
  action: "右手从被沿探入约二十厘米→停住",
  dialogue_raw: "—",
  dialogue: [],
  sound: "SFX·被子轻微的窸窣",
  transition: "硬切",
  light: "帐内暖光，光比4:1",
  color: "暖黄低饱和·试探",
  props: "帐纱",
  costume: "顾砚书-白开襟睡衣·素颜",
  fx: "—",
  beat: "HK",
  risk: "手不得露皮肤",
  material: "A055-025",
}

const DETAIL: SegmentDetail = {
  storyboard: {
    id: "55-4",
    ep: 55,
    no: 4,
    title: "第55集 第4段",
    source_path: "分镜/第55集_第4段_分镜脚本_60-90s.md",
    status: "READY",
    issues: [],
    shots: [
      SHOT,
      { ...SHOT, no: "SC01-SH026", seq: 26, framing: "CU", content: "她的眼睛从书上抬起" },
    ],
    range: { start: 60, end: 90 },
    duration_target: 30,
    aspect: "16:9",
    resolution: "1920×1080",
    carry_in: "他翻身坐起",
    props_tracking: [],
    risks: [],
  },
  materials: {
    rows: [
      { placeholder: "@人物1", name: "顾砚书", file: "45顾砚书睡衣.png", note: "角色三视图", kind: "person", missing: false, source: "auto" },
      { placeholder: "@道具1", name: "兽医书", file: "兽医书.png", note: "道具", kind: "prop", missing: false, source: "auto" },
      { placeholder: "@人物2", name: "Q版小人", file: "", note: "未匹配，请指定", kind: "person", missing: true, source: "missing" },
    ],
    name_to_placeholder: { 顾砚书: "@人物1", 兽医书: "@道具1" },
    scene_name: "西屋",
    scene_time: "夜",
    people: ["顾砚书", "Q版小人"],
    props: ["兽医书"],
    unmatched: [],
  },
  context: { style: "", scene: "", era_region: "", file: "" },
  size: { text: 0, assets: 6 },
}

const RENDERED: Rendered = {
  id: "55-4",
  file_stem: "04_30_01_060-090s",
  seg_name: "04_30_01",
  title: "04_30_01 · 60.0–90.0s（Seedance 三段式）",
  meta: "> 本段：30.0s / **2 镜**",
  tables: { materials: "", timeline: "", dialogue: "" },
  prefix: "【风格】\n16:9横屏，1985年中国苏北农村的年代剧。\n\n【主体描述】\n顾砚书 @人物1",
  body: "【时间轴】\n\n[镜1]中近景，俯拍，侧位，固定，长焦。",
  tail: "【镜头语言】\n本段 2 镜。【AI防崩约束词】\n1. 真人共 1 个。",
  full: [
    "# 04_30_01 · 60.0–90.0s（Seedance 三段式）",
    "",
    "## 前缀参考素材+风格+环境",
    "",
    "```",
    "【风格】",
    "16:9横屏，1985年中国苏北农村的年代剧。",
    "```",
    "",
    "## 正文",
    "",
    "```",
    "【时间轴】",
    "",
    "[镜1]中近景，俯拍，侧位，固定，长焦。",
    "```",
    "",
    "## 尾部模板",
    "",
    "```",
    "【镜头语言】",
    "本段 2 镜。",
    "```",
    "",
  ].join("\n"),
  issues: [{ kind: "MATERIAL_UNBOUND", field: "@人物2", detail: "Q版小人 没有匹配到文件" }],
  stats: { shots: 2, duration: 30, spoken: 1, materials: 3, unbound: 1 },
  source_path: "分镜/第55集_第4段_分镜脚本_60-90s.md",
  context: { style: "", scene: "", era_region: "", file: "" },
  storyboard: DETAIL.storyboard,
  materials: DETAIL.materials,
}

vi.mock("@/lib/promptsApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/promptsApi")>()
  return {
    ...actual,
    listSegments: vi.fn(async () => SEGMENTS),
    getSegment: vi.fn(async () => DETAIL),
    renderSegment: vi.fn(async () => RENDERED),
    savePrompt: vi.fn(async () => ({ path: "提示词/x.md", name: "x.md", bytes: 1 })),
    checkPrompt: vi.fn(async () => ({
      source: "draft" as const,
      chars: RENDERED.full.length,
      issues: [
        {
          code: "BODY_HAS_DURATION",
          level: "warn" as const,
          title: "正文写了时长",
          detail: "第 1 镜里出现了时长，时长只进动作表。",
          scope: "shot:1",
          line: 42,
          shot: 1,
          fixable: true,
          fix: { start: 0, end: 0, after: "" },
        },
      ],
      summary: { error: 0, warn: 1, info: 0, fixable: 1 },
    })),
    makePatch: vi.fn(async () => ({
      hunks: [
        {
          code: "BODY_HAS_DURATION",
          title: "正文写了时长",
          reason: "时长只进动作表",
          shot: 1,
          line: 42,
          start: 10,
          end: 20,
          before: "（约0秒—2.4秒）",
          after: "",
          context: "[镜1]（约0秒—2.4秒）中近景",
        },
      ],
      patched: `${RENDERED.full}\n<!-- patched -->\n`,
      applied: 1,
      skipped: [],
      issues_before: [],
      issues_after: [],
      summary_before: { error: 0, warn: 1, info: 0, fixable: 1 },
      summary_after: { error: 0, warn: 0, info: 0, fixable: 0 },
    })),
    listFiles: vi.fn(async () => [
      { path: "素材/24-Q版顾砚书.png", name: "24-Q版顾砚书.png", dir: "素材" },
      { path: "素材/45顾砚书睡衣.png", name: "45顾砚书睡衣.png", dir: "素材" },
    ]),
    setBinding: vi.fn(async (_seg: string, body: { placeholder: string }) => ({
      segment: "55-4",
      placeholder: body.placeholder,
      binding: null,
      cleared: true,
    })),
  }
})

beforeAll(() => {
  // 组件只在点「复制」时碰剪贴板，这里给个空壳，避免 jsdom 抛未实现
  Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => {}) } })
})

afterEach(cleanup)

import { PromptStudio } from "./PromptStudio"

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <PromptStudio />
    </QueryClientProvider>
  )
}

describe("提示词工程三栏", () => {
  it("左栏列出每一段与状态", async () => {
    mount()
    expect(await screen.findByText("提示词工程")).toBeTruthy()
    expect(await screen.findByText("第 4 段")).toBeTruthy()
    expect(screen.getByText("第 5 段")).toBeTruthy()
    expect(screen.getByText("部分解析")).toBeTruthy()
  })

  it("中栏默认是 21 字段分镜表", async () => {
    mount()
    expect(await screen.findByText("SC01-SH025")).toBeTruthy()
    expect(screen.getByText("SC01-SH026")).toBeTruthy()
    expect(screen.getByText("画面内容")).toBeTruthy()
  })

  it("右栏默认出【前缀】代码块", async () => {
    mount()
    expect(await screen.findByText(/16:9横屏，1985年中国苏北农村的年代剧/)).toBeTruthy()
  })

  it("切到素材页能看到待指定项", async () => {
    mount()
    await screen.findByText("SC01-SH025")
    fireEvent.click(screen.getByText(/^素材 3$/))
    expect((await screen.findAllByText("待指定")).length).toBeGreaterThan(0)
    expect(screen.getByText("45顾砚书睡衣.png")).toBeTruthy()
  })

  it("点一行弹出单镜详情，Esc 关闭", async () => {
    mount()
    fireEvent.click(await screen.findByText("SC01-SH025"))
    const dialog = await screen.findByRole("dialog")
    expect(dialog.textContent).toContain("画面内容")
    expect(dialog.textContent).toContain("他侧躺在帐里，右手从被子边缘探进去")
    expect(dialog.textContent).toContain("手不得露皮肤")
    fireEvent.keyDown(window, { key: "Escape" })
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
  })

  it("弹窗里四张主读卡齐全：画面 / 动作 / 场景状态 / 人物状态", async () => {
    mount()
    fireEvent.click(await screen.findByText("SC01-SH025"))
    const dialog = await screen.findByRole("dialog")
    expect(dialog.textContent).toContain("人物动作")
    expect(dialog.textContent).toContain("场景状态")
    expect(dialog.textContent).toContain("人物状态")
    // 场景状态把「光线 / 色彩」两列拼进同一张卡，场次退成抬头
    expect(dialog.textContent).toContain("帐内暖光，光比4:1")
    expect(dialog.textContent).toContain("暖黄低饱和·试探")
    expect(dialog.textContent).toContain("顾砚书-白开襟睡衣·素颜")
  })

  it("弹窗里能上一镜 / 下一镜", async () => {
    mount()
    fireEvent.click(await screen.findByText("SC01-SH025"))
    const first = await screen.findByRole("dialog")
    expect(first.getAttribute("aria-label")).toContain("SC01-SH025")
    fireEvent.click(screen.getByText("下一镜"))
    await waitFor(() =>
      expect(screen.getByRole("dialog").getAttribute("aria-label")).toContain("SC01-SH026")
    )
    fireEvent.click(screen.getByText("上一镜"))
    await waitFor(() =>
      expect(screen.getByRole("dialog").getAttribute("aria-label")).toContain("SC01-SH025")
    )
  })

  it("素材页能指定文件、也能忽略", async () => {
    mount()
    await screen.findByText("SC01-SH025")
    fireEvent.click(screen.getByText(/^素材 3$/))
    const row = (await screen.findByText("@人物2")).closest("tr") as HTMLElement

    fireEvent.click(within(row).getByText("指定"))
    const dialog = await screen.findByRole("dialog")
    expect(dialog.getAttribute("aria-label")).toContain("@人物2")
    fireEvent.click(await within(dialog).findByText("24-Q版顾砚书.png"))

    await waitFor(() => {
      const calls = vi.mocked(setBinding).mock.calls
      expect(calls.at(-1)?.[1]).toMatchObject({
        placeholder: "@人物2",
        file: "素材/24-Q版顾砚书.png",
      })
    })
    expect(vi.mocked(listFiles)).toHaveBeenCalled()

    const row2 = (await screen.findByText("@人物2")).closest("tr") as HTMLElement
    fireEvent.click(within(row2).getByText("忽略"))
    await waitFor(() => {
      const calls = vi.mocked(setBinding).mock.calls
      expect(calls.at(-1)?.[1]).toMatchObject({ placeholder: "@人物2", ignored: true })
    })
  })

  it("检查栏列出问题，能把补丁应用到草稿", async () => {
    mount()
    await screen.findByText("SC01-SH025")
    fireEvent.click(screen.getByText("检查"))

    expect(await screen.findByText("正文写了时长")).toBeTruthy()
    expect(vi.mocked(checkPrompt)).toHaveBeenCalled()
    expect(screen.getByText(/1 错误 · 0 提醒|0 错误 · 1 提醒/)).toBeTruthy()

    fireEvent.click(screen.getByText("生成修复补丁"))
    expect(await screen.findByText(/补丁预览：1 处/)).toBeTruthy()
    expect(screen.getByText("（约0秒—2.4秒）")).toBeTruthy()

    fireEvent.click(screen.getByText("应用到草稿"))
    await waitFor(() => expect(screen.getByText("未保存")).toBeTruthy())

    fireEvent.click(screen.getByText("全文"))
    const ta = (await screen.findByRole("textbox")) as HTMLTextAreaElement
    expect(ta.value).toContain("<!-- patched -->")
  })

  it("切到全文是整份 md，可编辑", async () => {
    mount()
    await screen.findByText("SC01-SH025")
    fireEvent.click(screen.getByText("全文"))
    const ta = (await screen.findByRole("textbox")) as HTMLTextAreaElement
    expect(ta.value).toContain("## 前缀参考素材+风格+环境")
    fireEvent.change(ta, { target: { value: ta.value + "\n改一笔\n" } })
    expect(screen.getByText("未保存")).toBeTruthy()
  })
})
