import { create } from "zustand"
import type { CardStatus } from "@/lib/types"
import { loadPrefs, savePrefs } from "@/lib/prefs"

export type View = "board" | "split" | "locator" | "assets" | "library" | "search" | "settings"

export interface CardFilter {
  status: CardStatus | "all"
  seg: string
  q: string
}

const EMPTY_FILTER: CardFilter = { status: "all", seg: "all", q: "" }

interface AppState {
  view: View
  setView: (v: View) => void

  filter: CardFilter
  setFilter: (patch: Partial<CardFilter>) => void
  resetFilter: () => void

  /** 拆分台 / 定位工单共用的当前段号 */
  seg: string
  setSeg: (seg: string) => void
}

/** 启动时从 localStorage 读回上次的视图与筛选（键 `gb.ui.v1`，见 lib/prefs.ts）。
 *  `keepOnReload` 关着时视图与筛选都不恢复 —— 设置页承诺「刷新后回到抽卡台」。 */
const boot = loadPrefs()
const bootView: View = boot.keepOnReload ? boot.view : "board"

export const useApp = create<AppState>((set) => ({
  view: bootView,
  setView: (v) => set({ view: v }),

  filter: boot.keepOnReload ? (boot.filter as CardFilter) : EMPTY_FILTER,
  setFilter: (patch) => set((s) => ({ filter: { ...s.filter, ...patch } })),
  resetFilter: () => set({ filter: EMPTY_FILTER }),

  seg: "",
  setSeg: (seg) => set({ seg }),
}))

/* 视图与筛选一改就存盘（200ms 防抖收口在 prefs 里）。
   `keepOnReload` 关着时由设置页负责不再读，不在这里特判。 */
useApp.subscribe((s) => {
  savePrefs({
    view: s.view,
    filter: { status: s.filter.status, seg: s.filter.seg, q: s.filter.q },
  })
})
