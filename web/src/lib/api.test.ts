/* api 客户端单测（错误分型 / 退避 / 超时 / 取消）。
 *
 * 跑法：npm test
 *
 * 用 node 自带的 test runner + 类型剥离跑，不引第三方测试框架（GB 前端
 * 一直是「零前端测试依赖」，这里保持这个惯例）。fetch 全部用桩替换，
 * 不碰真实后端。
 */
/* tsconfig.app.json 的 types 只给了 vite/client（浏览器侧不该看见 node 的类型）。
   这个文件是 node 跑的，显式把 node 的类型拉进来即可，不用改全局 tsconfig。 */
/// <reference types="node" />

import { test, beforeEach, afterEach } from "node:test"
import assert from "node:assert/strict"
import { api, ApiError, isAbortError } from "./api.ts"

const calls: { url: string; init?: RequestInit }[] = []
let impl: (url: string, init?: RequestInit, n?: number) => Promise<Response> = async () =>
  new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } })

const realFetch = globalThis.fetch

beforeEach(() => {
  calls.length = 0
  globalThis.fetch = async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init })
    return impl(String(url), init, calls.length)
  }
})

afterEach(() => {
  globalThis.fetch = realFetch
  impl = async () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } })
})

/** 把这些「注定要抛」的请求收成 ApiError，省得每处都写 unknown 断言。 */
function grab(p: Promise<unknown>): Promise<ApiError> {
  return p.then(
    () => { throw new Error("这一条本该抛错，却成功了") },
    (e: unknown) => e as ApiError
  )
}


const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })

test("4xx：kind=http，用后端 detail 当消息，且一次都不重试", async () => {
  impl = async () => json(400, { detail: "路径越出工作区：../secret.txt" })
  const e = await grab(api.get("/api/export"))
  assert.ok(e instanceof ApiError)
  assert.equal(e.kind, "http")
  assert.equal(e.status, 400)
  assert.equal(e.message, "路径越出工作区：../secret.txt")
  assert.equal(e.detail, "路径越出工作区：../secret.txt")
  // GET 默认有 2 次额度，但 4xx 不是瞬态失败 → 必须用掉的那一次就抛
  assert.equal(calls.length, 1)
})

test("状态码没人话可说时回落到 httpMessage", async () => {
  impl = async () => json(418, {})
  const e = await grab(api.get("/x"))
  assert.equal(e.kind, "http")
  assert.equal(e.message, "请求失败（HTTP 418）")
})

test("5xx：kind=server，GET 退避重试两次，间隔走 260×n²", async () => {
  impl = async () => json(500, { detail: "boom" })
  const t0 = Date.now()
  const e = await grab(api.get("/x"))
  const dt = Date.now() - t0
  assert.equal(e.kind, "server")
  assert.equal(e.status, 500)
  assert.equal(calls.length, 2)
  assert.ok(dt >= 200, `退避只有 ${dt}ms，没等到 260ms`)
})

test("写操作 5xx 不自动重试（重复提交会出两份任务）", async () => {
  impl = async () => json(500, { detail: "boom" })
  const e = await grab(api.post("/api/jobs", { chunk: "1of3" }))
  assert.equal(e.kind, "server")
  assert.equal(calls.length, 1)
})

test("网络错误：kind=network，说人话，不把 Failed to fetch 抛给用户", async () => {
  impl = async () => {
    throw new TypeError("Failed to fetch")
  }
  const e = await grab(api.get("/x"))
  assert.equal(e.kind, "network")
  assert.equal(e.status, 0)
  assert.match(e.message, /连不上后端服务/)
})

test("超时：到点 abort 掉在飞的请求，kind=timeout", async () => {
  impl = async (_u, init) =>
    new Promise<Response>((_res, rej) => {
      init?.signal?.addEventListener("abort", () => rej(new Error("aborted")))
    })
  const e = await grab(api.get("/x", { timeoutMs: 40 }))
  assert.equal(e.kind, "timeout")
  assert.match(e.message, /秒没回话/)
})

test("timeoutMs: 0 = 永不超时（给「选文件夹」这类等用户操作的请求）", async () => {
  let settled = false
  impl = async (_u, init) =>
    new Promise<Response>((res) => {
      // 120ms 后才回，比任何默认超时都短，但足以证明没被 0 掐掉
      setTimeout(() => {
        settled = true
        res(json(200, {}))
      }, 120)
      void init
    })
  await api.post("/api/system/pick-folder", {}, { timeoutMs: 0 })
  assert.equal(settled, true)
})

test("外部 signal 已 abort：请求压根不发", async () => {
  const ctrl = new AbortController()
  ctrl.abort()
  const e = await grab(api.get("/x", { signal: ctrl.signal }))
  assert.equal(e.kind, "aborted")
  assert.equal(isAbortError(e), true)
  assert.equal(calls.length, 0)
})

test("请求途中被 abort：kind=aborted，UI 据此静默", async () => {
  const ctrl = new AbortController()
  impl = async (_u, init) =>
    new Promise<Response>((_res, rej) => {
      init?.signal?.addEventListener("abort", () => rej(new Error("aborted")))
      setTimeout(() => ctrl.abort(), 20)
    })
  const e = await grab(api.get("/x", { signal: ctrl.signal }))
  assert.equal(e.kind, "aborted")
  assert.equal(isAbortError(e), true)
})

test("204 返回 undefined，不当成错误", async () => {
  impl = async () => new Response(null, { status: 204 })
  const r = await api.del("/api/jobs/1")
  assert.equal(r, undefined)
})

test("响应不是 JSON：kind=parse", async () => {
  impl = async () => new Response("<html>代理劫持了</html>", { status: 200 })
  const e = await grab(api.get("/x"))
  assert.equal(e.kind, "parse")
  assert.match(e.message, /不是 JSON/)
})

test("JSON 请求带 Content-Type；FormData 交给浏览器自己生成 boundary", async () => {
  impl = async () => json(200, { ok: true })
  await api.post("/x", { a: 1 })
  assert.equal((calls[0].init?.headers as Record<string, string>)["Content-Type"], "application/json")

  // 无 body 的写操作照旧发 `{}` + Content-Type：后端有些路由就是这么被调的
  await api.post("/api/workspaces/w/activate")
  assert.equal(calls[1].init?.body, "{}")
  assert.equal((calls[1].init?.headers as Record<string, string>)["Content-Type"], "application/json")
  // GET / DELETE 才是真的不带 body
  await api.get("/x")
  assert.equal(calls[2].init?.body, undefined)
  assert.equal(calls[2].init?.headers, undefined)

  const fd = new FormData()
  fd.append("file", new Blob(["x"]), "a.txt")
  await api.upload("/api/assets/upload", fd)
  const h = calls[3].init?.headers as Record<string, string> | undefined
  assert.ok(!h || !("Content-Type" in h), "上传不能手写 Content-Type")
})
