import { createRoute, createRouter, redirect } from "@tanstack/react-router"
import { rootRoute } from "@/routes/root"
import { assetsRoute } from "@/routes/assets"
import { searchRoute } from "@/routes/search"
import { promptsRoute } from "@/routes/prompts"
import { promptSegRoute } from "@/routes/prompts.$seg"
import { reviewRoute } from "@/routes/review"
import { reviewDetailRoute } from "@/routes/review.$seg.$block"
import { produceRoute } from "@/routes/produce"
import { settingsRoute } from "@/routes/settings"

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/assets", replace: true })
  },
})

const routeTree = rootRoute.addChildren([
  indexRoute,
  assetsRoute,
  searchRoute,
  promptsRoute,
  promptSegRoute,
  reviewRoute,
  reviewDetailRoute,
  produceRoute,
  settingsRoute,
])

export const router = createRouter({
  routeTree,
  defaultPreload: "intent",
})

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router
  }
}
