import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Health } from "@/lib/types"

/**
 * 后端健康状态：常驻轮询（补 G-8）。
 *
 * 原来的 `/api/health` 只有启动页消费一次，之后 Rail 上的 ffmpeg / ComfyUI
 * 指示灯就永远是启动那一刻的快照——用户中途改了 ffmpeg 路径、或者后端进程
 * 挂了，界面上看不出来。这个 hook 让健康状态变成「活的」：
 *
 * - 在线时 20s 一探（配置改了能被看见）；
 * - 掉线后 5s 一探（尽快发现恢复）；
 * - 窗口重新聚焦时立刻重探（refetchOnWindowFocus 默认开）；
 * - 🔴 用 TanStack 的 `status === "error"` 判断离线，**不看 isFetching**——
 *   后台刷新时 data 还在，不能闪成离线。
 *
 * 注意：queryKey 与 App 启动时的探测是同一个，两边共享同一份缓存；
 * App 只在该查询「从来没成功过」时才占满屏（Splash），连上之后后端再掉线
 * 由 Rail 的健康灯说话，页面照常留着。
 */
export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => api.get<Health>("/api/health"),
    retry: 1,
    staleTime: 10_000,
    refetchInterval: (q) => (q.state.status === "error" ? 5_000 : 20_000),
  })
}
