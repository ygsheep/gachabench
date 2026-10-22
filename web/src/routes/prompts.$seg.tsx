import { createRoute } from "@tanstack/react-router"
import { SplitPanel } from "@/pages/SplitPanel"
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
    <SplitPanel
      seg={seg}
      onSegChange={(next) => {
        void navigate({ to: "/prompts/$seg", params: { seg: next }, replace: true })
      }}
    />
  )
}
