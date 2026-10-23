// @vitest-environment jsdom
import { useState } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { Card, CardStatus } from "@/lib/types"
import type { CardFilter } from "@/store/useApp"
import { ReviewDesk } from "@/pages/ReviewDesk"

/* 三栏各自出什么：
   左 = 段列表（每段的块用实心/虚线小方块表示齐缺），
   中 = 当前段的块列 × 候选条，右 = 选中那一条的详情。
   不测样式，只钉住「哪一栏该出现什么」，防止以后改布局把某一栏整块弄丢。 */

const REJECT_TAGS = ["手部穿插", "表情不对"]

/* 假后端：工厂被提升到 import 之前执行，所以数据走 vi.hoisted 的盒子，
   等 CARDS 定义完再塞进去。 */
const box = vi.hoisted(() => ({ cards: [] as unknown[], tags: [] as string[] }))

vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(async (path: string) => {
      if (path.startsWith("/api/cards")) return { cards: box.cards, reject_tags: box.tags }
      return {}
    }),
    post: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
  },
}))

function card(id: number, seg: string, chunk: string, status: CardStatus = "draft"): Card {
  return {
    id,
    workspace: "复试2",
    rel_path: `草稿/段${seg}_${chunk}_${id}.mp4`,
    seg,
    chunk,
    status,
    reject_tags: status === "reject" ? ["手部穿插"] : [],
    reject_note: "",
    tags: [`SH${seg}01`],
    note: "",
    prompt_md: null,
    split_plan_id: null,
    duration: 5,
    width: 1280,
    height: 720,
    has_audio: 1,
    created_at: "2026-10-20 10:00:00",
    updated_at: "2026-10-20 10:00:00",
  }
}

// 段 04：三块齐，每块两条；段 05：缺第三块
const CARDS: Card[] = [
  card(1, "04", "1of3", "final"),
  card(2, "04", "1of3"),
  card(3, "04", "2of3"),
  card(4, "04", "2of3"),
  card(5, "04", "3of3", "reject"),
  card(6, "05", "1of3"),
  card(7, "05", "2of3"),
]
box.cards.push(...CARDS)
box.tags.push(...REJECT_TAGS)

afterEach(cleanup)

function Harness() {
  const [filter, setFilter] = useState<CardFilter>({ status: "all", seg: "all", q: "" })
  return (
    <ReviewDesk
      workspace="复试2"
      filter={filter}
      onFilterChange={(patch) => setFilter((f) => ({ ...f, ...patch }))}
    />
  )
}

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <Harness />
    </QueryClientProvider>
  )
}

/** 左栏的段按钮：只有它是以段号开头、且撑满整栏的那个 */
function segButton(seg: string): HTMLElement {
  const hit = screen
    .getAllByRole("button")
    .find((b) => b.textContent?.trim().startsWith(seg) && b.className.includes("w-full"))
  if (!hit) throw new Error(`找不到段 ${seg} 的按钮`)
  return hit
}

describe("审片台", () => {
  it("三栏都在：段列表 / 块列 / 这一条", async () => {
    mount()
    await waitFor(() => expect(screen.getByText("审片台")).toBeTruthy())

    // 左：两段都列出来
    await waitFor(() => expect(segButton("04")).toBeTruthy())
    expect(segButton("05")).toBeTruthy()

    // 中：当前段（04）的三块列头 + 齐块提示
    await waitFor(() => expect(screen.getByText("3/3 齐")).toBeTruthy())
    expect(screen.getAllByText("1of3").length).toBeGreaterThan(0)
    expect(screen.getAllByText("3of3").length).toBeGreaterThan(0)

    // 右：默认选中第一条（已经是正片，按钮就该说「已是正片」）
    expect(screen.getByText("这一条")).toBeTruthy()
    expect(screen.getByRole("button", { name: "已是正片" })).toBeTruthy()
  })

  it("点段切到那一段，缺块要说得出来", async () => {
    mount()
    await waitFor(() => expect(segButton("05")).toBeTruthy())

    fireEvent.click(segButton("05"))

    // 05 段只有两块：缺块提示出现，齐块提示消失
    await waitFor(() => expect(screen.getByText("缺 3of3")).toBeTruthy())
    expect(screen.queryByText("3/3 齐")).toBeNull()
  })

  it("状态筛选只收窄候选，不改段里的块数", async () => {
    mount()
    await waitFor(() => expect(screen.getByText("3/3 齐")).toBeTruthy())

    fireEvent.click(screen.getByRole("button", { name: /正片\s*1/ }))

    // 04 段只剩一张正片；块还是三块，另外两块如实说「无正片」
    await waitFor(() => expect(screen.getAllByText("无正片").length).toBe(2))
    expect(screen.getByText("3/3 齐")).toBeTruthy()
  })
})
