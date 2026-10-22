import { createRoute, redirect } from "@tanstack/react-router"
import { rootRoute } from "./root"

/** P0：审片详情先回到审片台，用 search 参数带上段与块。 */
export const reviewDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/review/$seg/$block",
  beforeLoad: ({ params }) => {
    throw redirect({
      to: "/review",
      search: { seg: params.seg, block: params.block },
      replace: true,
    })
  },
})
