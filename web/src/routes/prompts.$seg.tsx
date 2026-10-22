import { createRoute } from "@tanstack/react-router"
import { PromptStudio } from "@/pages/PromptStudio"
import { rootRoute } from "./root"

export const promptSegRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/prompts/$seg",
  component: PromptSegRoute,
})

function PromptSegRoute() {
  const { seg } = promptSegRoute.useParams()
  const navigate = promptSegRoute.useNavigate()
  return (
    <PromptStudio
      seg={seg}
      onSegChange={(next) => {
        void navigate({ to: "/prompts/$seg", params: { seg: next }, replace: true })
      }}
    />
  )
}
