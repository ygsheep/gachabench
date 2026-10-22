import { describe, expect, it } from "vitest"
import { LEGACY_HASH_TARGETS, legacyHashKey } from "./legacy-hash"

describe("旧 hash 入口", () => {
  it("七个旧入口都能认出", () => {
    const keys = ["board", "split", "locator", "assets", "library", "search", "settings"]
    for (const key of keys) {
      expect(legacyHashKey(`#/${key}`)).toBe(key)
    }
  })

  it("落到约定的新地址", () => {
    expect(LEGACY_HASH_TARGETS).toEqual({
      board: "/review",
      split: "/prompts",
      locator: "/prompts",
      assets: "/assets",
      library: "/assets?collection=novel",
      search: "/search",
      settings: "/settings",
    })
  })

  it("容忍无斜杠、尾部斜杠与查询串", () => {
    expect(legacyHashKey("#board")).toBe("board")
    expect(legacyHashKey("#/board/")).toBe("board")
    expect(legacyHashKey("#/assets?collection=novel")).toBe("assets")
  })

  it("也认 TanStack Router 去掉 # 后的 hash", () => {
    expect(legacyHashKey("/search")).toBe("search")
    expect(legacyHashKey("board")).toBe("board")
  })

  it("未知 hash 不跳转", () => {
    expect(legacyHashKey("")).toBeNull()
    expect(legacyHashKey("#/unknown")).toBeNull()
  })
})
