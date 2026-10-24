// @vitest-environment jsdom
/**
 * 收件箱横带的接线测试。跑法：npm run test:ui
 *
 * 钉住四件事，都是「改布局时最容易整块丢」的：
 * ① 条数来自 `status=draft`；② 「通过」打的是 to-final 且默认复制；
 * ③ 「废稿」走 PATCH status=reject（不删文件）；④ 超过 12 条给一句剩余提示。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { Card, CardStatus } from "@/lib/types"
import { InboxStrip } from "@/components/InboxStrip"

const box = vi.hoisted(() => ({
  cards: [] as unknown[],
  dirs: [] as string[],
  posts: [] as { path: string; body: unknown }[],
  patches: [] as { path: string; body: unknown }[],
}))

vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(async (path: string) => {
      if (path.startsWith("/api/cards")) return { cards: box.cards, reject_tags: [] }
      if (path.includes("watch-dirs")) return { name: "复试2", watch_dirs: box.dirs }
      return {}
    }),
    post: vi.fn(async (path: string, body?: unknown) => {
      box.posts.push({ path, body })
      return { card: {}, copied_to: "素材/未分类/17.1_成片.mp4" }
    }),
    patch: vi.fn(async (path: string, body?: unknown) => {
      box.patches.push({ path, body })
      return {}
    }),
  },
}))

function card(id: number, status: CardStatus = "draft"): Card {
  return {
    id,
    workspace: "复试2",
    rel_path: `草稿/17_第17段_${id}.mp4`,
    seg: "17",
    chunk: "1of3",
    status,
    reject_tags: [],
    reject_note: "",
    tags: [],
    note: "",
    prompt_md: null,
    split_plan_id: null,
    has_prompt_snapshot: false,
    duration: 5,
    width: 1920,
    height: 1080,
    has_audio: 1,
    created_at: "2026-10-24T01:00:00",
    updated_at: "2026-10-24T01:00:00",
  }
}

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <InboxStrip workspace="复试2" />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  box.cards = []
  box.dirs = []
  box.posts = []
  box.patches = []
})

afterEach(() => cleanup())

describe("InboxStrip", () => {
  it("条数来自草稿，卡片按后端顺序排出来", async () => {
    box.cards = [card(1), card(2)]
    mount()
    await waitFor(() => expect(screen.getByTitle("草稿里还没定性的条数").textContent).toBe("2"))
    expect(screen.getAllByText("通过").length).toBe(2)
    expect(screen.getAllByText("废稿").length).toBe(2)
  })

  it("通过 = to-final + 默认复制（不传就不复制是旧缺陷，这里必须显式给）", async () => {
    box.cards = [card(7)]
    mount()
    await waitFor(() => expect(screen.getByText("通过")).toBeTruthy())
    fireEvent.click(screen.getByText("通过"))

    await waitFor(() => expect(box.posts.length).toBe(1))
    expect(box.posts[0].path).toBe("/api/cards/7/to-final")
    expect(box.posts[0].body).toEqual({ copy_to_final: true })
  })

  it("废稿 = PATCH status=reject，不动文件", async () => {
    box.cards = [card(9)]
    mount()
    await waitFor(() => expect(screen.getByText("废稿")).toBeTruthy())
    fireEvent.click(screen.getByText("废稿"))

    await waitFor(() => expect(box.patches.length).toBe(1))
    expect(box.patches[0].path).toBe("/api/cards/9")
    expect(box.patches[0].body).toMatchObject({ status: "reject" })
  })

  it("空收件箱说一句实话，不留空白", async () => {
    mount()
    await waitFor(() => expect(screen.getByText(/收件箱空了/)).toBeTruthy())
    expect(screen.getByTitle("草稿里还没定性的条数").textContent).toBe("0")
  })

  it("超过 12 条只排前 12 条，剩下给一句剩余数", async () => {
    box.cards = Array.from({ length: 15 }, (_, i) => card(i + 1))
    mount()
    await waitFor(() => expect(screen.getByText(/还有 3 条/)).toBeTruthy())
    expect(screen.getAllByText("通过").length).toBe(12)
  })

  it("配了采集源才让点「收编一次」", async () => {
    box.cards = [card(1)]
    mount()
    const btn = await screen.findByText("收编一次")
    expect((btn as HTMLButtonElement).disabled).toBe(true)
    // 采集源是另一个请求，等它落地再断言文案
    expect(await screen.findByText("未配置采集源")).toBeTruthy()
  })
})
