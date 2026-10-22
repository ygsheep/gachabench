import { useEffect, useRef } from "react"
import type { AssetChangedEvent, AssetProgressEvent } from "@/lib/types"

/**
 * 订阅 `/api/events` 的资产索引事件。
 *
 * 约束：
 * - 回调用 ref 存，父组件重渲染不会重建 EventSource；
 * - 组件卸载时 close()，并摘掉监听；
 * - `onerror` 什么都不做 —— EventSource 自己按 retry 重连，这里抛错只会污染控制台。
 */
export interface AssetEventsHandlers {
  onProgress?: (e: AssetProgressEvent) => void
  onChanged?: (e: AssetChangedEvent) => void
  /** 默认 true；关掉时立即断开（例如没有激活工作区） */
  enabled?: boolean
  /** 默认 `/api/events`；单独抽出来便于测试或反代前缀 */
  url?: string
}

function parse<T>(event: MessageEvent): T | null {
  if (typeof event.data !== "string") return null
  try {
    const data = JSON.parse(event.data) as T
    return data && typeof data === "object" ? data : null
  } catch {
    return null
  }
}

export function useAssetEvents(handlers: AssetEventsHandlers): void {
  const ref = useRef(handlers)
  useEffect(() => {
    ref.current = handlers
  })

  const enabled = handlers.enabled ?? true
  const url = handlers.url ?? "/api/events"

  useEffect(() => {
    if (!enabled) return
    if (typeof EventSource === "undefined") return

    const es = new EventSource(url)

    const onProgress = (event: Event) => {
      const data = parse<AssetProgressEvent>(event as MessageEvent)
      if (data) ref.current.onProgress?.(data)
    }
    const onChanged = (event: Event) => {
      const data = parse<AssetChangedEvent>(event as MessageEvent)
      if (data) ref.current.onChanged?.(data)
    }

    es.addEventListener("index.progress", onProgress)
    es.addEventListener("file.changed", onChanged)
    // 断线是常态（后端重启 / 休眠）。EventSource 自带重连，这里静默。
    es.onerror = () => {}

    return () => {
      es.removeEventListener("index.progress", onProgress)
      es.removeEventListener("file.changed", onChanged)
      es.close()
    }
  }, [enabled, url])
}
