import { createRoute } from "@tanstack/react-router"
import { SettingsPage } from "@/pages/SettingsPage"
import { useActiveWorkspace } from "@/lib/workspace"
import { rootRoute } from "./root"

export const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  component: SettingsRoute,
})

function SettingsRoute() {
  const workspace = useActiveWorkspace()
  return <SettingsPage workspace={workspace} />
}
