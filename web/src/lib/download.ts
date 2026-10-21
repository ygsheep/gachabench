/**
 * 导出通道（前端侧）：把 `/api/export` 的三态契约变成用户能看到的结果。
 *
 * 来源：novel-booksource-engine 的下载逻辑，按 GB 重排
 * （见 docs/INTEGRATION-novel-booksource-engine.md §4.5）。
 *
 * ## 为什么 web 形态不能直接 `fetch` 一个文件流
 *
 * 浏览器对「不可渲染」的响应体（`video/mp4`、`application/zip`）会**接管为原生下载**，
 * 于是 `fetch` 只能拿到一个空的 204 —— 表现是下载下来一个 0 字节文件，而代码还以为
 * 成功了。所以后端一律用 JSON + base64 交字节，这里负责解回 Blob 再触发下载。
 *
 * ## 桌面形态为什么不走 WebView 下载
 *
 * Tauri 的 WebView 对 `<a download>` 的支持取决于 WebView2 版本，不可靠。
 * 桌面形态由前端先问用户要一个目录（复用 `/api/system/pick-folder` 的原生对话框），
 * 再让后端直接写盘 —— 后端就跑在本机，这条路比壳层 `save_export` command 更短，
 * 也少一个要随 Tauri 编译发布的部件。
 */

import { api } from "@/lib/api"

/** 后端 `/api/export` 的三种返回形态。 */
export type ExportPayload =
  | {
      kind: "download"
      filename: string
      mime: string
      size: number
      base64: string
      /** 打成了 zip 时，里面装了几个条目 */
      packed?: number
    }
  | { kind: "file"; path: string; filename: string; size: number }
  | { kind: "dir"; path: string; count: number }

export type ExportItem =
  | { rel_path: string; filename?: string; mime?: string; subdir?: string }
  | { text: string; filename: string; mime?: string; subdir?: string }

/** 范围导出（C4）：后端自己枚举条目，前端只说「第几到几」。 */
export type ExportRangeParams = {
  what: "prompts" | "chapters" | "cards"
  from?: number
  to?: number
  book_id?: number
  status?: "final" | "draft" | "reject" | "all"
  seg?: string
}

/** 是否跑在 Tauri 壳里（桌面上 `window.__TAURI_INTERNALS__` 一定存在）。 */
export function isDesktop(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window
}

/** base64 → Blob。atob 出来是 latin1 字节串，必须逐字节码进 Uint8Array。 */
export function base64ToBlob(base64: string, mime: string): Blob {
  const bin = atob(base64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i)
  return new Blob([bytes], { type: mime || "application/octet-stream" })
}

/** 走浏览器下载（web 形态）。 */
export function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  a.rel = "noopener"
  document.body.appendChild(a)
  a.click()
  // 🔴 不能同步 remove()：个别浏览器（含 WebView2）会把「点了就消失的 anchor」
  // 当成无效点击，下载还没开始就被取消。也不宜立刻 revokeObjectURL。
  // 所以两者都推迟到一分钟后 —— 那时候下载早就开始了。
  setTimeout(() => {
    a.remove()
    URL.revokeObjectURL(url)
  }, 60_000)
}

/** 弹原生目录选择框（阻塞直到用户选完 / 取消）。取消返回 null。 */
export async function pickExportDir(initial = ""): Promise<string | null> {
  try {
    const r = await api.post<{ path: string | null; cancelled: boolean }>(
      "/api/system/pick-folder",
      { title: "选择导出目录", initial },
      // 🔴 原生对话框要等用户操作，多慢都可能 —— 绝不能给它套默认超时
      { timeoutMs: 0 }
    )
    return r.path ?? null
  } catch {
    return null
  }
}

export type ExportOutcome =
  | { ok: true; kind: "download" | "dir" | "file"; message: string; path?: string }
  | { ok: false; message: string }

/**
 * 统一导出入口。
 *
 * - web：直接要 download 载荷，解成 Blob 触发下载。
 * - desktop：先问目录，再让后端写盘；用户取消就安静返回，不当成错误。
 */
export async function exportItems(
  items: ExportItem[],
  opts: { desktop?: boolean; preferDir?: boolean; zipName?: string } = {}
): Promise<ExportOutcome> {
  if (!items.length) return { ok: false, message: "没有可导出的内容" }

  const destDir = await maybePickDir(opts)
  if (destDir === CANCELLED) return { ok: false, message: "已取消导出" }

  let payload: ExportPayload
  try {
    payload = await postExport("/api/export", { items, dest_dir: destDir, zip_name: opts.zipName })
  } catch (e) {
    return { ok: false, message: (e as Error).message }
  }
  return consume(payload)
}

/** 范围导出（C4）。与 `exportItems` 共用后半段，只是条目的来源不同。 */
export async function exportRange(
  params: ExportRangeParams,
  opts: { desktop?: boolean; preferDir?: boolean; zipName?: string } = {}
): Promise<ExportOutcome> {
  const destDir = await maybePickDir(opts)
  if (destDir === CANCELLED) return { ok: false, message: "已取消导出" }

  let payload: ExportPayload
  try {
    payload = await postExport("/api/export/range", {
      what: params.what,
      from: params.from ?? 1,
      to: params.to ?? 1,
      book_id: params.book_id ?? null,
      status: params.status ?? "final",
      seg: params.seg ?? "",
      dest_dir: destDir,
      zip_name: opts.zipName,
    })
  } catch (e) {
    return { ok: false, message: (e as Error).message }
  }
  return consume(payload)
}

const CANCELLED = "\u0000cancelled"

/** 桌面形态（或调用方指名要目录）时先问目录；web 形态返回 null 走下载。 */
async function maybePickDir(opts: {
  desktop?: boolean
  preferDir?: boolean
}): Promise<string | null | typeof CANCELLED> {
  const desktop = opts.desktop ?? isDesktop()
  if (!(desktop || opts.preferDir === true)) return null
  const dir = await pickExportDir()
  // 用户取消：不是错误，别弹红条
  return dir ?? CANCELLED
}

function postExport(path: string, body: Record<string, unknown>): Promise<ExportPayload> {
  // 后端上限 256MB：读大文件 + base64 可能远超默认 60s，给到 5 分钟
  return api.post<ExportPayload>(path, body, { timeoutMs: 300_000 })
}

/** 把三态载荷变成用户能看懂的一句话。 */
function consume(payload: ExportPayload): ExportOutcome {
  if (payload.kind === "download") {
    const blob = base64ToBlob(payload.base64, payload.mime)
    if (blob.size === 0) return { ok: false, message: "导出内容是空的" }
    triggerDownload(blob, payload.filename)
    const n = payload.packed
    return {
      ok: true,
      kind: "download",
      message: n ? `已开始下载 ${payload.filename}（${n} 个文件打包）` : `已开始下载 ${payload.filename}`,
    }
  }
  if (payload.kind === "dir") {
    return {
      ok: true,
      kind: "dir",
      path: payload.path,
      message: `已导出 ${payload.count} 个文件到 ${payload.path}`,
    }
  }
  return {
    ok: true,
    kind: "file",
    path: payload.path,
    message: `已导出到 ${payload.path}`,
  }
}
