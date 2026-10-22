/**
 * P2 文件操作接口封装：计划 / 应用 / 回滚、回收站、重复文件。
 *
 * 这里同时收敛三件容易被页面写散的事情：
 *   1) 计划请求体的归一化（字段按 kind 取，ID 去重、目录反斜杠转正）；
 *   2) 后端 `detail: {code, message}` 的错误解析（ApiError 里可能只拿到 JSON 字符串）；
 *   3) 应用/回滚后需要失效的 query key 清单。
 */
import type { QueryClient } from "@tanstack/react-query"
import { api, ApiError, type RequestOpts } from "./api.ts"
import { ASSETS_KEY_ROOT } from "./assetsApi.ts"
import type {
  AssetOpsKind,
  DuplicatesResp,
  OpsPlan,
  OpsPlanBody,
  TrashListResp,
  TrashPurgeResp,
  TrashRestoreResp,
  UnreferencedResp,
} from "./types.ts"

export type { AssetOpsKind }

/** 计划请求的三种入参（页面侧用这个，而不是直接拼请求体） */
export type OpsPlanInput =
  | { kind: "rename"; assetId: string; newName: string }
  | { kind: "move"; assetIds: string[]; destDir: string }
  | { kind: "delete"; assetIds: string[] }

/**
 * 归一化目标目录：只做前端能确定的事。
 * 越界（`..` / 绝对路径 / 盘符）交给后端返回 PATH_INVALID，前端不抢先改写语义。
 */
export function normalizeDestDir(raw: string): string {
  let dir = String(raw ?? "").replace(/\\/g, "/").trim()
  if (dir === "." || dir === "./") return ""
  while (dir.length > 1 && dir.endsWith("/")) dir = dir.slice(0, -1)
  return dir
}

/** 归一化计划请求体：ID 去重去空、文件名/目录 trim。非法输入直接抛中文错误。 */
export function normalizePlanBody(input: OpsPlanInput): OpsPlanBody {
  if (input.kind === "rename") {
    const assetId = input.assetId.trim()
    const newName = input.newName.trim()
    if (!assetId) throw new Error("请选择要改名的资产")
    if (!newName) throw new Error("新文件名不能为空")
    return { kind: "rename", asset_id: assetId, new_name: newName }
  }

  const assetIds = [
    ...new Set(input.assetIds.map((id) => String(id ?? "").trim()).filter(Boolean)),
  ]
  if (!assetIds.length) throw new Error("请至少选择一个资产")

  if (input.kind === "move") {
    return { kind: "move", asset_ids: assetIds, dest_dir: normalizeDestDir(input.destDir) }
  }
  return { kind: "delete", asset_ids: assetIds }
}

/* ------------------------------------------------------------------------- */
/* 查询 key                                                                   */
/* ------------------------------------------------------------------------- */

export const TRASH_KEY = ["asset-trash"] as const
export const DUPLICATES_KEY = ["asset-duplicates"] as const
export const UNREFERENCED_KEY = ["asset-unreferenced"] as const
/** 详情 query key 的前缀：["index-asset", workspace, assetId] */
export const INDEX_ASSET_KEY = ["index-asset"] as const

/** 任何文件操作成功后统一失效的范围（列表 / 集合 / 详情 / 回收站 / 重复） */
export function invalidateAfterAssetOps(qc: QueryClient): void {
  void qc.invalidateQueries({ queryKey: ASSETS_KEY_ROOT })
  void qc.invalidateQueries({ queryKey: ["asset-collections"] })
  void qc.invalidateQueries({ queryKey: INDEX_ASSET_KEY })
  void qc.invalidateQueries({ queryKey: TRASH_KEY })
  void qc.invalidateQueries({ queryKey: DUPLICATES_KEY })
  void qc.invalidateQueries({ queryKey: UNREFERENCED_KEY })
}

/* ------------------------------------------------------------------------- */
/* 接口                                                                       */
/* ------------------------------------------------------------------------- */

export function createOpsPlan(input: OpsPlanInput, opts?: RequestOpts): Promise<OpsPlan> {
  return api.post<OpsPlan>("/api/assets/operations/plan", normalizePlanBody(input), opts)
}

export function applyOpsPlan(planId: string, opts?: RequestOpts): Promise<OpsPlan> {
  return api.post<OpsPlan>(
    `/api/assets/operations/${encodeURIComponent(planId)}/apply`,
    undefined,
    opts
  )
}

export function rollbackOpsPlan(planId: string, opts?: RequestOpts): Promise<OpsPlan> {
  return api.post<OpsPlan>(
    `/api/assets/operations/${encodeURIComponent(planId)}/rollback`,
    undefined,
    opts
  )
}

export function getOpsPlan(planId: string, opts?: RequestOpts): Promise<OpsPlan> {
  return api.get<OpsPlan>(`/api/assets/operations/${encodeURIComponent(planId)}`, opts)
}

export function getTrash(opts?: RequestOpts): Promise<TrashListResp> {
  return api.get<TrashListResp>("/api/trash", opts)
}

export function restoreTrash(id: string, opts?: RequestOpts): Promise<TrashRestoreResp> {
  return api.post<TrashRestoreResp>(`/api/trash/${encodeURIComponent(id)}/restore`, undefined, opts)
}

export function purgeTrash(id: string, opts?: RequestOpts): Promise<TrashPurgeResp> {
  return api.del<TrashPurgeResp>(`/api/trash/${encodeURIComponent(id)}`, opts)
}

export function getDuplicates(opts?: RequestOpts): Promise<DuplicatesResp> {
  return api.get<DuplicatesResp>("/api/assets/duplicates", opts)
}

export function getUnreferenced(limit = 200, opts?: RequestOpts): Promise<UnreferencedResp> {
  return api.get<UnreferencedResp>(
    `/api/assets/unreferenced?limit=${Math.max(1, Math.floor(limit))}`,
    opts
  )
}

/* ------------------------------------------------------------------------- */
/* 错误文案                                                                   */
/* ------------------------------------------------------------------------- */

const ERROR_TEXT: Record<string, string> = {
  PLAN_STALE: "计划已过期，请重新计算影响面",
  PLAN_NOT_APPLICABLE: "计划已应用或已回滚，不能再次应用",
  PLAN_NOT_APPLIED: "只有已应用的计划可以撤销",
  PLAN_BLOCKED: "计划里有不可执行的项，请重新计算影响面",
  NAME_CONFLICT: "目标文件名已被占用",
  NAME_INVALID: "文件名不合法",
  FILE_BUSY: "文件正被其他程序占用，关闭后再试",
  PATH_INVALID: "目标目录不合法，只接受工作区内的相对路径",
  BASE_CHANGED: "文件在计算影响面之后发生了变化，请重新计算",
  REWRITE_FAILED: "引用改写失败，已回滚",
  APPLY_FAILED: "应用失败，已回滚",
  ROLLBACK_FAILED: "撤销失败，请检查文件是否被占用",
  TRASH_NOT_FOUND: "回收站条目不存在",
  TRASH_RESTORED: "条目已恢复，不能彻底清除",
  ASSET_NOT_FOUND: "资产不存在，刷新后再试",
  ASSET_NOT_ACTIVE: "只有正常状态的资产可以执行这个操作",
  INVALID_REQUEST: "请求参数不完整",
  UNKNOWN_KIND: "不支持的操作类型",
}

/** 解析后端 `detail: {code, message}`；detail 可能是被 JSON.stringify 过的字符串。 */
export function parseOpsError(error: unknown): { code: string; message: string } {
  if (error instanceof ApiError) {
    const raw = error.detail || error.message
    try {
      const parsed: unknown = JSON.parse(raw)
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        const obj = parsed as Record<string, unknown>
        const code = typeof obj.code === "string" ? obj.code : ""
        const message = typeof obj.message === "string" ? obj.message : ""
        if (code || message) {
          return { code, message: message || ERROR_TEXT[code] || raw }
        }
      }
    } catch {
      // 不是 JSON：退回原文（后端有些 400 直接给字符串）
    }
    return { code: "", message: raw }
  }
  if (error instanceof Error) return { code: "", message: error.message }
  return { code: "", message: "操作失败，后端没有给出原因" }
}

/** 把错误码翻成中文可读提示；已知错误码优先，保证提示稳定。 */
export function opsErrorMessage(error: unknown): string {
  const { code, message } = parseOpsError(error)
  return ERROR_TEXT[code] || message || "操作失败"
}

/* ------------------------------------------------------------------------- */
/* 行级 diff                                                                  */
/* ------------------------------------------------------------------------- */

export interface DiffLine {
  kind: "same" | "add" | "del"
  beforeNo: number | null
  afterNo: number | null
  text: string
}

/** 去掉末尾换行产生的空行（保留文件中间的空行） */
function splitLines(text: string): string[] {
  const lines = String(text ?? "").split(/\r?\n/)
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop()
  return lines
}

/**
 * 简单逐行对比：只用于展示改名计划里 before / after 的差异。
 *
 * 改写只发生在文件名区间内，绝大多数行号一一对应；这里再加一层相邻行探测，
 * 让「多一行 / 少一行」也能显示成增删而不是整段替换。没有引入 diff 库。
 */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = splitLines(before)
  const b = splitLines(after)
  const rows: DiffLine[] = []
  let i = 0
  let j = 0

  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      rows.push({ kind: "same", beforeNo: i + 1, afterNo: j + 1, text: a[i] })
      i += 1
      j += 1
    } else if (i + 1 < a.length && a[i + 1] === b[j]) {
      rows.push({ kind: "del", beforeNo: i + 1, afterNo: null, text: a[i] })
      i += 1
    } else if (j + 1 < b.length && b[j + 1] === a[i]) {
      rows.push({ kind: "add", beforeNo: null, afterNo: j + 1, text: b[j] })
      j += 1
    } else {
      rows.push({ kind: "del", beforeNo: i + 1, afterNo: null, text: a[i] })
      rows.push({ kind: "add", beforeNo: null, afterNo: j + 1, text: b[j] })
      i += 1
      j += 1
    }
  }

  while (i < a.length) {
    rows.push({ kind: "del", beforeNo: i + 1, afterNo: null, text: a[i] })
    i += 1
  }
  while (j < b.length) {
    rows.push({ kind: "add", beforeNo: null, afterNo: j + 1, text: b[j] })
    j += 1
  }
  return rows
}
