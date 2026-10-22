import { createRoute } from "@tanstack/react-router"
import { PromptStudio } from "@/pages/PromptStudio"
import { rootRoute } from "./root"

export const promptsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/prompts",
  component: PromptsRoute,
})

function PromptsRoute() {
  const navigate = promptsRoute.useNavigate()
  return (
    <PromptStudio
      onSegChange={(seg) => {
        void navigate({ to: "/prompts/$seg", params: { seg }, replace: true })
      }}
    />
  )
}
