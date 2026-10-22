/** 旧 hash 入口到新路径的映射（P0 兼容用，P8 删除）。 */
export type LegacyKey =
  | "board"
  | "split"
  | "locator"
  | "assets"
  | "library"
  | "search"
  | "settings"

export const LEGACY_HASH_TARGETS: Record<LegacyKey, string> = {
  board: "/review",
  split: "/produce?tab=split",
  locator: "/prompts",
  assets: "/assets",
  library: "/assets?collection=novel",
  search: "/search",
  settings: "/settings",
}

/**
 * `#/board`、`#board`、`#/board?x=1` 都归一到 key；不认识返回 null。
 *
 * 浏览器给的是带 `#` 的串，而 TanStack Router 的 `location.hash` 已经去掉 `#`
 * （只剩 `/board`），两种形态都要认。
 */
export function legacyHashKey(hash: string): LegacyKey | null {
  const key = hash
    .replace(/^#/, "")
    .replace(/^\//, "")
    .replace(/[?#].*$/, "")
    .replace(/\/+$/, "")
  return key in LEGACY_HASH_TARGETS ? (key as LegacyKey) : null
}
