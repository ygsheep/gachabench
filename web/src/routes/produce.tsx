import { createRoute } from "@tanstack/react-router"
import { cn } from "cn"
import { SplitPanel } from "@/pages/SplitPanel"
import { rootRoute } from "./root"

/*
 * 「制作」= 拆分台 + 任务台。
 *
 * 拆分台原先占着一级入口，但它只是「出片前把 15s 切成 3 块」这一件事，
 * 和跑片、看队列是同一段工作流。P3 起收进制作：拆分是准备，提交与产物是结果。
 * 一级导航因此只剩「提示词工程 / 制作 / 审片台」三段主线。
 */

export type ProduceTab = "split" | "jobs"

const TABS = new Set<string>(["split", "jobs"])

export interface ProduceSearch {
  tab?: ProduceTab
  seg?: string
}

export const produceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/produce",
  validateSearch: (search: Record<string, unknown>): ProduceSearch => ({
    tab:
      typeof search.tab === "string" && TABS.has(search.tab)
        ? (search.tab as ProduceTab)
        : undefined,
    seg: typeof search.seg === "string" ? search.seg : undefined,
  }),
  component: ProducePage,
})

function ProducePage() {
  const search = produceRoute.useSearch()
  const navigate = produceRoute.useNavigate()
  const tab: ProduceTab = search.tab ?? "split"

  const setTab = (next: ProduceTab) =>
    void navigate({ search: (prev) => ({ ...prev, tab: next }), replace: true })

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex flex-wrap items-end gap-x-4 gap-y-2 px-6 pb-1 pt-5">
        <div className="max-w-[74ch]">
          <h1 className="text-[19px] font-semibold leading-tight">制作</h1>
          <p className="pt-0.5 text-[12.5px] text-ash">
            先把 15 秒切成三块，再交给任务队列。切点只落在镜的自然边界，正文一个词都不改。
          </p>
        </div>
        <span className="flex-1" />
        <div className="inline-flex rounded-ctl border border-rule bg-cell p-0.5">
          {(
            [
              ["split", "拆分台"],
              ["jobs", "任务台"],
            ] as [ProduceTab, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              aria-pressed={tab === key}
              className={cn(
                "h-7 rounded-[5px] px-3 text-[12px] transition-colors",
                tab === key ? "bg-hi font-medium text-paper" : "text-ash hover:text-paper"
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </header>

      {tab === "split" ? (
        <SplitPanel
          bare
          seg={search.seg}
          onSegChange={(seg) =>
            void navigate({ search: (prev) => ({ ...prev, seg }), replace: true })
          }
        />
      ) : (
        <JobsPlaceholder />
      )}
    </div>
  )
}

function JobsPlaceholder() {
  return (
    <div className="grid min-h-0 flex-1 place-items-center p-8">
      <div className="max-w-[46ch] rounded-panel border border-rule-soft bg-panel p-6 text-center">
        <h2 className="text-[14px] font-medium">任务台</h2>
        <p className="mt-2 text-[12.5px] leading-relaxed text-ash">
          队列、参数覆盖、产物回收在 P7 接入。现在跑片仍从拆分台逐块提交，产物照旧进草稿。
        </p>
      </div>
    </div>
  )
}
