import { createRoute } from "@tanstack/react-router"
import { NovelSearch } from "@/pages/NovelSearch"
import { rootRoute } from "./root"

export const searchRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/search",
  component: NovelSearch,
})
