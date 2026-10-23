import { createContext, useContext, useMemo, type ReactNode } from "react"

/** 根布局解析出的当前工作区：name 用于所有接口，title 用于所有显示。 */
interface WorkspaceCtx {
  name: string | null
  title: string
}

const WorkspaceContext = createContext<WorkspaceCtx>({ name: null, title: "" })

/**
 * 🔴 name 与 title 必须分开：
 *   - name = 目录名，同时是 `cards.db` 里 workspace 列的键、URL 路径段、接口参数；
 *   - title = 用户可改的显示名（`Workspace.label`，空则回落到 name）。
 * 把 title 拿去拼接口地址，改个名字就会全站 404。
 */
export function WorkspaceProvider({
  value,
  title,
  children,
}: {
  value: string | null
  title?: string
  children: ReactNode
}) {
  const ctx = useMemo<WorkspaceCtx>(
    () => ({ name: value, title: (title ?? "").trim() || value || "" }),
    [value, title]
  )
  return <WorkspaceContext.Provider value={ctx}>{children}</WorkspaceContext.Provider>
}

/** 当前工作区的**目录名**（接口参数、缓存键都用它）。 */
export function useActiveWorkspace(): string | null {
  return useContext(WorkspaceContext).name
}

/** 当前工作区的**显示名**（界面上给人看的那一个）。 */
export function useWorkspaceTitle(): string {
  return useContext(WorkspaceContext).title
}

/** 取某个工作区的显示名：label（去空白）优先，否则目录名。 */
export function wsTitle(w: { name: string; label?: string } | null | undefined): string {
  if (!w) return ""
  return (w.label ?? "").trim() || w.name
}
