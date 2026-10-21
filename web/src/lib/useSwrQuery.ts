import { useEffect } from "react"
import { useQuery, type QueryKey, type UseQueryOptions, type UseQueryResult } from "@tanstack/react-query"
import { swrRead, swrWrite } from "@/lib/swrCache"

/**
 * 带 SWR 缓存的 useQuery。
 *
 * - 冷启动（QueryCache 空）时用 localStorage 旧数据当 placeholderData，先渲染后刷新；
 * - 请求成功后把结果写进缓存；placeholder 本身不写回（避免把旧数据又存一遍）；
 * - 之后「扫描 / 上传后 invalidate」的刷新由 TanStack 自己处理（保留旧数据 + 后台
 *   refetch），与本 hook 无关。
 *
 * 调用方据此显示「旧结果 · 更新中」徽标：`isFetching && data` 即为真。
 * 徽标样式必须用虚线（`border-dashed`）—— GB 的轮廓语言里虚线 = 未确认。
 *
 * queryFn 拿到 TanStack 给的 `signal`：查询被取消（切工作区、被 invalidate
 * 挤掉）时这个 signal 会 abort，正在飞的那个请求就真的被掐掉，而不是白跑一趟
 * 还占着连接。把它传给 `api.get(url, { signal })` 即可。
 */
export function useSwrQuery<T>(
  key: string,
  queryKey: readonly unknown[],
  queryFn: (signal: AbortSignal) => Promise<T>,
  opts?: { enabled?: boolean }
): UseQueryResult<T> {
  // 函数形式：只有在 QueryCache 里没有这个 key 的数据时才会调用。
  // placeholderData 的类型签名要求「非 undefined」，而缓存未命中时我们就是要给
  // undefined —— 这里 as 一下收口（返回值的运行时语义由 swrRead 保证：null → undefined）。
  const options = {
    queryKey,
    queryFn: ({ signal }: { signal: AbortSignal }) => queryFn(signal),
    enabled: opts?.enabled,
    placeholderData: () => swrRead<T>(key) ?? undefined,
  } as UseQueryOptions<T, Error, T, QueryKey>

  const q = useQuery(options)

  useEffect(() => {
    if (q.data !== undefined && !q.isPlaceholderData) swrWrite(key, q.data)
  }, [key, q.data, q.isPlaceholderData])

  return q
}
