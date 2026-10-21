import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { api } from "@/lib/api"
import type { AssetUploadResp, MediaKind } from "@/lib/types"

/**
 * 素材上传的落名规则 —— 上传对话框、拖放到卡片、Ctrl+V 粘贴三条路径共用这一份，
 * 保证「同一个文件不管从哪个入口进来，落到磁盘的名字完全一样」。
 */

export const EXT_KIND: Record<string, MediaKind> = {
  ".png": "image",
  ".jpg": "image",
  ".jpeg": "image",
  ".webp": "image",
  ".bmp": "image",
  ".gif": "image",
  ".mp3": "audio",
  ".wav": "audio",
  ".m4a": "audio",
  ".aac": "audio",
  ".flac": "audio",
  ".ogg": "audio",
  ".mp4": "video",
  ".mov": "video",
  ".webm": "video",
  ".mkv": "video",
}

/** 剪贴板来的图/音没有可信文件名，退回 MIME 猜扩展名 */
const MIME_EXT: Record<string, string> = {
  "image/png": ".png",
  "image/jpeg": ".jpg",
  "image/jpg": ".jpg",
  "image/webp": ".webp",
  "image/gif": ".gif",
  "image/bmp": ".bmp",
  "audio/mpeg": ".mp3",
  "audio/mp3": ".mp3",
  "audio/wav": ".wav",
  "audio/x-wav": ".wav",
  "audio/mp4": ".m4a",
  "audio/aac": ".aac",
  "audio/flac": ".flac",
  "audio/ogg": ".ogg",
  "video/mp4": ".mp4",
  "video/quicktime": ".mov",
  "video/webm": ".webm",
}

export const DEFAULT_SUFFIX: Record<string, string> = { image: "参考", audio: "配音", video: "参考" }
export const KIND_LABEL: Record<string, string> = {
  image: "图片",
  audio: "音频",
  video: "视频",
  other: "其它",
}
export const FILE_ACCEPT = "image/*,audio/*,video/*"

/** 与后端 _sanitize_part 同口径：去保留字符、并空白、去首尾点与空格 */
export function sanitizePart(s: string): string {
  return s
    .replace(/[\\/:*?"<>|]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^[.\s]+/, "")
    .replace(/[.\s]+$/, "")
}

export function extOf(file: File): string {
  const i = file.name.lastIndexOf(".")
  if (i > 0) {
    const e = file.name.slice(i).toLowerCase()
    if (EXT_KIND[e]) return e
  }
  return MIME_EXT[(file.type || "").toLowerCase()] ?? (i > 0 ? file.name.slice(i).toLowerCase() : "")
}

/** 后端按文件名后缀判类型 —— 没有扩展名的（多是剪贴板图片）先补一个 */
export function ensureNamed(file: File): File {
  const ext = extOf(file)
  if (!ext) return file
  if (file.name.toLowerCase().endsWith(ext)) return file
  const base = file.name.replace(/\.[^.]*$/, "") || "pasted"
  try {
    return new File([file], base + ext, { type: file.type })
  } catch {
    return file
  }
}

export function fileKey(f: File): string {
  return `${f.name}:${f.size}:${f.lastModified}`
}

export interface ResolvedRow {
  file: File
  kind: MediaKind
  name: string
  deduped: boolean
  reason: string | null
}

/** 保存前把真实落名算出来（含重名补号）；服务端仍是权威，这里只是让用户先看见 */
export function resolveRows(
  files: File[],
  opts: { prefix: string; suffix: string; mode: "attach" | "new"; taken: Set<string> }
): ResolvedRow[] {
  const used = new Set(opts.taken)
  return files.map((file) => {
    const ext = extOf(file)
    const kind = EXT_KIND[ext]
    if (!kind) {
      return {
        file,
        kind: "other" as MediaKind,
        name: file.name,
        deduped: false,
        reason: `不支持的格式 ${ext || "(无扩展名)"}`,
      }
    }
    const sfx = opts.suffix || (opts.mode === "new" ? "" : (DEFAULT_SUFFIX[kind] ?? "参考"))
    const stem = [opts.prefix, sfx].filter(Boolean).join("_")
    if (!stem) {
      return { file, kind, name: file.name, deduped: false, reason: "先填主体名" }
    }
    let name = stem + ext
    let deduped = false
    let i = 2
    while (used.has(name)) {
      name = `${stem}_${String(i).padStart(2, "0")}${ext}`
      i += 1
      deduped = true
    }
    used.add(name)
    return { file, kind, name, deduped, reason: null }
  })
}

export interface UploadPayload {
  prefix: string
  suffix: string
  /** character / scene / style / other —— 新主体建目录时用 */
  category: string
  /** 可选提示：留空则由后端自动定位该主体已有目录 */
  subdir: string
  files: File[]
}

export function postAssets(p: UploadPayload): Promise<AssetUploadResp> {
  const fd = new FormData()
  fd.append("prefix", p.prefix)
  fd.append("suffix", p.suffix)
  fd.append("category", p.category)
  fd.append("subdir", p.subdir)
  // 所有入口都在这里收口：没有扩展名的（剪贴板图片）先补一个，后端要按后缀判类型
  for (const f of p.files) fd.append("files", ensureNamed(f))
  // 不要手写 Content-Type，boundary 得让浏览器自己生成
  return api.upload<AssetUploadResp>("/api/assets/upload", fd)
}

export function reportUpload(res: AssetUploadResp) {
  const n = res.items.length
  const dn = res.items.filter((i) => i.deduped).length
  const auto = res.auto_placed && res.dir ? "（自动定位）" : ""
  const where = `${res.asset_dir}/${res.dir ? `${res.dir}/` : ""}${auto}`
  const skipped = res.skipped.map((s) => `${s.name}：${s.reason}`).join("；")
  if (n) {
    toast.success(`已保存 ${n} 个文件 → ${where}`, {
      description: dn ? `其中 ${dn} 个重名，已自动补号（_02 …）` : res.items.map((i) => i.name).join("、"),
    })
  }
  if (res.skipped.length) {
    if (n) toast.warning(`${res.skipped.length} 个文件没进去`, { description: skipped })
    else toast.error("没有文件被保存", { description: skipped })
  }
}

/** 上传动作 + 自动刷新素材库 + 结果反馈 */
export function useAssetUpload() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: postAssets,
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["assets"] })
      reportUpload(res)
    },
    onError: (e) => {
      toast.error("上传失败", { description: (e as Error).message })
    },
  })
}

/** 从拖放 / 剪贴板事件里取出文件（剪贴板里的图常常只在 items 里） */
export function filesFromDataTransfer(dt: DataTransfer | null): File[] {
  if (!dt) return []
  const out: File[] = []
  const items = dt.items ? Array.from(dt.items) : []
  for (const it of items) {
    if (it.kind !== "file") continue
    const f = it.getAsFile()
    if (f) out.push(f)
  }
  if (!out.length && dt.files) out.push(...Array.from(dt.files))
  return out
}

export function isFileDrag(e: DragEvent): boolean {
  const types = e.dataTransfer?.types
  return !!types && Array.from(types).includes("Files")
}
