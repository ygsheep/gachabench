import { create } from "zustand"
import type { CardStatus } from "@/lib/types"
import { loadPrefs, savePrefs } from "@/lib/prefs"

export interface CardFilter {
  status: CardStatus | "all"
  seg: string
  q: string
}

const EMPTY_FILTER: CardFilter = { status: "all", seg: "all", q: "" }

interface AppState {
  filter: CardFilter
  setFilter: (patch: Partial<CardFilter>) => void
  resetFilter: () => void

  /** 拆分台当前段号（P0 仍与路由参数同步） */
  seg: string
  setSeg: (seg: string) => void
}

/** 启动时从 localStorage 读回筛选（键 `gb.ui.v1`，见 lib/prefs.ts）。
 *  页面位置由 URL 决定，不再从这里恢复视图。 */
const boot = loadPrefs()

export const useApp = create<AppState>((set) => ({
  filter: boot.keepOnReload ? (boot.filter as CardFilter) : EMPTY_FILTER,
  setFilter: (patch) => set((s) => ({ filter: { ...s.filter, ...patch } })),
  resetFilter: () => set({ filter: EMPTY_FILTER }),

  seg: "",
  setSeg: (seg) => set({ seg }),
}))

/* 筛选一改就存盘（200ms 防抖收口在 prefs 里）。 */
useApp.subscribe((s) => {
  savePrefs({
    filter: { status: s.filter.status, seg: s.filter.seg, q: s.filter.q },
  })
})
