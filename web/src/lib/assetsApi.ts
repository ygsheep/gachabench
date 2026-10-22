/**
 * P1 资产索引接口封装。
 *
 * 与旧分组接口（`/api/assets/groups`）刻意分开：这里的 `/api/assets` 是
 * 分页索引，路径拼接与 query key 都收敛在下面这几个纯函数里，便于单测。
 */
import { api, type RequestOpts } from "./api.ts"
import type {
  AssetScanStartResp,
  AssetScanStatusResp,
  IndexAssetDetail,
  IndexAssetListResp,
  IndexCollectionsResp,
  IndexMetaPatch,
} from "./types.ts"

export interface AssetsQueryParams {
  role?: string
  category?: string
  collection?: string
  q?: string
  cursor?: string
  limit?: number
}

/** 去掉空串 / 纯空白 / 非法 limit，避免把 `role=` 这类空参数写进 URL。 */
export function normalizeAssetsParams(params: AssetsQueryParams): AssetsQueryParams {
  const out: AssetsQueryParams = {}
  const role = typeof params.role === "string" ? params.role.trim() : ""
  const category = typeof params.category === "string" ? params.category.trim() : ""
  const collection = typeof params.collection === "string" ? params.collection.trim() : ""
  const q = typeof params.q === "string" ? params.q.trim() : ""
  const cursor = typeof params.cursor === "string" ? params.cursor.trim() : ""
  if (role) out.role = role
  if (category) out.category = category
  if (collection) out.collection = collection
  if (q) out.q = q
  if (cursor) out.cursor = cursor
  if (typeof params.limit === "number" && Number.isFinite(params.limit) && params.limit > 0) {
    out.limit = Math.floor(params.limit)
  }
  return out
}

/** 拼列表 URL；没有有效参数时连 `?` 都不出现。 */
export function buildAssetsQuery(params: AssetsQueryParams): string {
  const p = normalizeAssetsParams(params)
  const sp = new URLSearchParams()
  if (p.role) sp.set("role", p.role)
  if (p.category) sp.set("category", p.category)
  if (p.collection) sp.set("collection", p.collection)
  if (p.q) sp.set("q", p.q)
  if (p.cursor) sp.set("cursor", p.cursor)
  if (p.limit) sp.set("limit", String(p.limit))
  const qs = sp.toString()
  return qs ? `/api/assets?${qs}` : "/api/assets"
}

/** 列表 query key 的前缀 —— invalidation 只用这一段即可覆盖所有筛选组合。 */
export const ASSETS_KEY_ROOT = ["index-assets"] as const

/**
 * 稳定 query key：参数顺序不同、空白不同都归一成同一份。
 * 分页游标与 limit 不参与 key（同一份筛选共用一个无限列表）。
 */
export function assetsKey(params: AssetsQueryParams): readonly unknown[] {
  const p = normalizeAssetsParams({ ...params, cursor: undefined, limit: undefined })
  return [ASSETS_KEY_ROOT[0], "list", p]
}

export function getAssets(
  params: AssetsQueryParams,
  opts?: RequestOpts
): Promise<IndexAssetListResp> {
  return api.get<IndexAssetListResp>(buildAssetsQuery(params), opts)
}

export function getAsset(
  id: string,
  opts?: RequestOpts
): Promise<IndexAssetDetail> {
  return api.get<IndexAssetDetail>(`/api/assets/${encodeURIComponent(id)}`, opts)
}

export function patchAsset(
  id: string,
  patch: IndexMetaPatch,
  opts?: RequestOpts
): Promise<IndexAssetDetail> {
  return api.patch<IndexAssetDetail>(`/api/assets/${encodeURIComponent(id)}`, patch, opts)
}

export function getAssetCollections(opts?: RequestOpts): Promise<IndexCollectionsResp> {
  return api.get<IndexCollectionsResp>("/api/assets/collections", opts)
}

export function startAssetScan(
  force: boolean,
  opts?: RequestOpts
): Promise<AssetScanStartResp> {
  return api.post<AssetScanStartResp>("/api/assets/scan", { force }, opts)
}

export function getAssetScanStatus(
  taskId: string,
  opts?: RequestOpts
): Promise<AssetScanStatusResp> {
  return api.get<AssetScanStatusResp>(
    `/api/assets/scan/${encodeURIComponent(taskId)}`,
    opts
  )
}
