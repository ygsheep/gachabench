import { useMemo } from "react"
import { createRoute } from "@tanstack/react-router"
import { GachaBoard } from "@/pages/GachaBoard"
import { useActiveWorkspace } from "@/lib/workspace"
import type { CardFilter } from "@/store/useApp"
import type { CardStatus } from "@/lib/types"
import { rootRoute } from "./root"

export interface ReviewSearch {
  status?: CardStatus | "all"
  seg?: string
  q?: string
  block?: string
}

const STATUSES = new Set<string>(["all", "draft", "final", "reject"])

function status(v: unknown): CardStatus | "all" | undefined {
  return typeof v === "string" && STATUSES.has(v) ? (v as CardStatus | "all") : undefined
}

export const reviewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/review",
  validateSearch: (search: Record<string, unknown>): ReviewSearch => ({
    status: status(search.status),
    seg: typeof search.seg === "string" ? search.seg : undefined,
    q: typeof search.q === "string" ? search.q : undefined,
    block: typeof search.block === "string" ? search.block : undefined,
  }),
  component: ReviewRoute,
})

function ReviewRoute() {
  const workspace = useActiveWorkspace()
  const search = reviewRoute.useSearch()
  const navigate = reviewRoute.useNavigate()

  const filter = useMemo<CardFilter>(
    () => ({
      status: search.status ?? "all",
      seg: search.seg ?? "all",
      q: search.q ?? "",
    }),
    [search.status, search.seg, search.q]
  )

  return (
    <GachaBoard
      workspace={workspace}
      filter={filter}
      onFilterChange={(patch) => {
        void navigate({
          search: (prev) => ({ ...prev, ...patch }),
          replace: true,
        })
      }}
    />
  )
}
