import { createRoute } from "@tanstack/react-router"
import { AssetIndex } from "@/pages/AssetIndex"
import { rootRoute } from "./root"

export interface AssetsSearch {
  role?: string
  category?: string
  collection?: string
  q?: string
  /** 一级目录 tab：`素材`（当前集要用的）或 `资产`（全量资产库）。 */
  dir?: string
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
    dir: str(search.dir),
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
          // 只是改 URL 上的筛选 / 打开某张卡，不是换页面：绝不能把视口滚回顶部。
          // 滚动复位只应该发生在「用户真的换了筛选」时，由列表内部自己处理。
          resetScroll: false,
        })
      }}
    />
  )
}
