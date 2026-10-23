/**
 * 复制图片到系统剪贴板。
 *
 * 走浏览器 Clipboard API（`navigator.clipboard.write` + `ClipboardItem`）：
 * 把图片字节写进剪贴板后，可以直接 Ctrl+V 粘到微信、Word、PS、画图等任何地方。
 *
 * ## 为什么图片要「先 fetch 成 blob 再写」
 *
 * `navigator.clipboard.write` 只吃 `ClipboardItem`（内包 Blob），不吃 URL。而图片
 * 存在后端 `/api/media?path=`，好在 Vite 开发代理与生产同源，`fetch` 拿 blob 没有
 * 跨域问题 —— 这正是能直接复制的原因。
 *
 * ## 只支持单张（重要）
 *
 * 本项目跑在 Tauri WebView2 里，实测 `navigator.clipboard.write([item1, item2, ...])`
 * 会抛 `Support for multiple ClipboardItems is not implemented` —— 这个内核**不支持**
 * 一次写多个条目。所以这里**只写单张**：多张选中时也只保留最后一张，并明确提示，
 * 绝不假装多张都复制成功。
 */

export type CopyImageResult =
  | { ok: true; count: number; message?: string }
  | { ok: false; message: string }

/** 单个图片 URL → Blob。失败（404 / 网络）抛出带可读信息的 Error。 */
async function fetchImageBlob(url: string): Promise<Blob> {
  const res = await fetch(url)
  if (!res.ok) {
    throw new Error(`图片读不到（HTTP ${res.status}）`)
  }
  const blob = await res.blob()
  if (blob.size === 0) {
    throw new Error("图片内容是空的")
  }
  return blob
}

/** 把一张图的 blob 写进剪贴板（单条目，任何 Chromium/WebView 都支持）。 */
async function writeSingle(blob: Blob): Promise<void> {
  const png = await toPngBlob(blob)
  await navigator.clipboard.write([new ClipboardItem({ "image/png": png })])
}

/** 复制一张图片到剪贴板。 */
export async function copyImageToClipboard(url: string): Promise<CopyImageResult> {
  try {
    if (!("clipboard" in navigator) || !("ClipboardItem" in window)) {
      return { ok: false, message: "当前浏览器不支持复制图片到剪贴板" }
    }
    const blob = await fetchImageBlob(url)
    await writeSingle(blob)
    return { ok: true, count: 1 }
  } catch (e) {
    return { ok: false, message: clipboardErrorText(e) }
  }
}

/**
 * 复制图片到剪贴板。当前内核不支持多条目，这里只写**最后一张**：
 * 调用方（资产页）会在多选时用返回值里的 `count < urls.length` 判断并提示。
 */
export async function copyImagesToClipboard(urls: string[]): Promise<CopyImageResult> {
  try {
    if (!urls.length) return { ok: false, message: "没有可复制的图片" }
    if (!("clipboard" in navigator) || !("ClipboardItem" in window)) {
      return { ok: false, message: "当前浏览器不支持复制图片到剪贴板" }
    }
    // 只取最后一张：多条目写在这个内核会直接抛 NotImplemented，干脆不做。
    const last = urls[urls.length - 1]
    const blob = await fetchImageBlob(last)
    await writeSingle(blob)
    return {
      ok: true,
      count: urls.length,
      // 让调用方能识别「多选但只复制了最后一张」并给出诚实提示
      message: urls.length > 1 ? "当前环境只支持复制单张，已复制最后选中的那张" : undefined,
    }
  } catch (e) {
    return { ok: false, message: clipboardErrorText(e) }
  }
}

/** 把抛出的异常翻译成用户能看懂的一句话。 */
function clipboardErrorText(e: unknown): string {
  const msg = (e as Error)?.message || String(e)
  if (msg.includes("permission") || msg.includes("NotAllowed")) return "剪贴板权限被拒绝"
  if (msg.includes("multiple ClipboardItems")) return "当前环境不支持一次复制多张，请单张复制"
  return msg
}

/** 任意图片 blob → PNG blob。canvas 解码再 encode 成 png，统一 MIME。 */
async function toPngBlob(blob: Blob): Promise<Blob> {
  // 已经是 png 就直接用，省一次解码（GIF 等会被 canvas 静默转成第一帧）。
  if (blob.type === "image/png") return blob
  try {
    const bmp = await createImageBitmap(blob)
    const canvas = document.createElement("canvas")
    canvas.width = bmp.width
    canvas.height = bmp.height
    const ctx = canvas.getContext("2d")
    if (!ctx) return blob
    ctx.drawImage(bmp, 0, 0)
    bmp.close()
    const png = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/png")
    )
    return png ?? blob
  } catch {
    // createImageBitmap 失败（罕见格式）时退回原始 blob，写剪贴板时仍标 image/png，
    // 由浏览器兜底处理；真写不进去会走 clipboardErrorText 报错。
    return blob
  }
}
