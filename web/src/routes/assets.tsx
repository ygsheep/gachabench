import { createRoute } from "@tanstack/react-router"
import { AssetIndex } from "@/pages/AssetIndex"
import { rootRoute } from "./root"

export interface AssetsSearch {
  role?: string
  category?: string
  collection?: string
  q?: string
  asset?: string
  /** 旧 hash 入口 `collection=novel&panel=search` 兼容用，页面不消费 */
  panel?: string
}

function str(v: unknown): string | undefined {
  return typeof v === "string" ? v : undefined
}

export const assetsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/assets",
  validateSearch: (search: Record<string, unknown>): AssetsSearch => ({
    role: str(search.role),
    category: str(search.category),
    collection: str(search.collection),
    q: str(search.q),
    asset: str(search.asset),
    panel: str(search.panel),
  }),
  component: AssetsRoute,
})

function AssetsRoute() {
  const search = assetsRoute.useSearch()
  const navigate = assetsRoute.useNavigate()
  return (
    <AssetIndex
      search={search}
      onSearchChange={(patch) => {
        void navigate({
          search: (prev) => ({ ...prev, ...patch }),
          replace: true,
        })
      }}
    />
  )
}
