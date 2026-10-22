import { createRoute } from "@tanstack/react-router"
import { SplitPanel } from "@/pages/SplitPanel"
import { rootRoute } from "./root"

export const promptsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/prompts",
  component: PromptsRoute,
})

function PromptsRoute() {
  const navigate = promptsRoute.useNavigate()
  return (
    <SplitPanel
      onSegChange={(seg) => {
        void navigate({ to: "/prompts/$seg", params: { seg } })
      }}
    />
  )
}
