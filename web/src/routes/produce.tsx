import { createRoute } from "@tanstack/react-router"
import { rootRoute } from "./root"

export const produceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/produce",
  component: ProduceRoute,
})

function ProduceRoute() {
  return (
    <div className="grid min-h-0 flex-1 place-items-center p-8">
      <div className="max-w-[46ch] rounded-panel border border-rule-soft bg-panel p-6 text-center">
        <h1 className="text-[15px] font-medium">制作</h1>
        <p className="mt-2 text-[12.5px] leading-relaxed text-ash">
          任务台建设中。提交、队列与产物回收在 P7 接入。
        </p>
      </div>
    </div>
  )
}
