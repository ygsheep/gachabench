import { createContext, useContext, type ReactNode } from "react"

/** 根布局解析出的当前工作区名；页面路由从这里取，避免每页重查一次工作区列表。 */
const WorkspaceContext = createContext<string | null>(null)

export function WorkspaceProvider({
  value,
  children,
}: {
  value: string | null
  children: ReactNode
}) {
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}

export function useActiveWorkspace(): string | null {
  return useContext(WorkspaceContext)
}
