/* fetch 封装：错误分型 + 超时 + 退避重试 + 可取消。
 *
 * 思路移植自 novel-booksource-engine 的 lib/api.js，按 GB 的情况改了三点：
 *   1) 🔴 只有 GET 自动重试。写操作（POST/PATCH/PUT/DELETE）重复提交会出两份
 *      任务、两份文件，一次都不自动重试——NBE 那边「非 4xx 就重试」不能照搬。
 *   2) 默认超时按方法分档：GET 30s / 写 60s / 上传 5min；「选文件夹」这类
 *      **等用户操作**的请求必须显式给 `timeoutMs: 0`，否则弹窗还没点完就被掐。
 *   3) 错误自带 kind（network/timeout/http/server/aborted/parse），UI 直接读
 *      err.message 就能上 toast，不用再对着 "Failed to fetch" 猜。
 *
 * 兼容性：`api.get<T>(path)` 这类老写法全部照旧，第二个参数才是 opts。
 */

export type ApiErrorKind =
  | "network" // 连不上（后端没起 / 端口不对 / 代理劫持）
  | "timeout" // 到点没回话
  | "http" // 4xx：调用方的问题，不重试
  | "server" // 5xx：后端的问题，可重试
  | "aborted" // 被 AbortSignal 取消（不是错误，UI 别弹 toast）
  | "parse" // 响应不是 JSON
  | "unknown"

export class ApiError extends Error {
  /** HTTP 状态码；0 = 压根没拿到响应（网络 / 超时 / 取消） */
  status: number
  kind: ApiErrorKind
  /** 后端 detail 原文（可能为空） */
  detail: string

  constructor(
    message: string,
    meta: { status?: number; kind?: ApiErrorKind; detail?: string } = {}
  ) {
    super(message)
    this.name = "ApiError"
    this.status = meta.status ?? 0
    this.kind = meta.kind ?? "unknown"
    this.detail = meta.detail ?? ""
  }
}

/** 判定「这次失败只是被取消了」——调用方据此静默掉，别弹 toast。 */
export function isAbortError(e: unknown): boolean {
  return e instanceof ApiError && e.kind === "aborted"
}

export interface RequestOpts {
  /** 毫秒；0 = 永不超时（给等用户操作的请求） */
  timeoutMs?: number
  /** 最多尝试几次；默认 GET 2 次、写操作 1 次 */
  attempts?: number
  signal?: AbortSignal
  headers?: Record<string, string>
}

const T_GET = 30_000
const T_WRITE = 60_000
const T_UPLOAD = 300_000
const ATTEMPTS_GET = 2

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

function defaultTimeout(method: string): number {
  return method === "GET" ? T_GET : T_WRITE
}

function httpMessage(status: number): string {
  if (status === 400) return "请求参数有误"
  if (status === 401 || status === 403) return "没有权限"
  if (status === 404) return "接口不存在，后端版本可能不匹配"
  if (status === 409) return "状态冲突，刷新后再试"
  if (status === 422) return "请求参数未通过校验"
  if (status >= 500) return "后端服务异常，请查看日志"
  return `请求失败（HTTP ${status}）`
}

async function readErrorPayload(res: Response): Promise<string> {
  try {
    const data = await res.json()
    if (data && typeof data.detail === "string") return data.detail
    if (data && typeof data.message === "string") return data.message
    if (typeof data === "string") return data
    // 没有 detail/message 时只保留「有内容的」负载：空对象 {} 原样 stringify
    // 出来是 "{}"，这种消息抛给用户纯属噪音，不如回落到 httpMessage。
    if (data && typeof data === "object" && Object.keys(data).length) return JSON.stringify(data)
    return ""
  } catch {
    return ""
  }
}

/** 只有「后端可能只是抖了一下」的失败才值得再试一次。 */
function isTransient(e: ApiError): boolean {
  return e.kind === "network" || e.kind === "timeout" || e.kind === "server"
}

async function request<T>(
  path: string,
  method: string,
  body: unknown,
  opts: RequestOpts = {}
): Promise<T> {
  const timeoutMs = opts.timeoutMs ?? defaultTimeout(method)
  const attempts = Math.max(1, opts.attempts ?? (method === "GET" ? ATTEMPTS_GET : 1))
  const { signal } = opts
  let lastErr: ApiError | null = null

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const ctrl = new AbortController()
    const forward = () => ctrl.abort()
    if (signal) {
      if (signal.aborted) throw new ApiError("请求已取消", { kind: "aborted" })
      signal.addEventListener("abort", forward, { once: true })
    }

    let timedOut = false
    let timer: ReturnType<typeof setTimeout> | undefined
    if (timeoutMs > 0) {
      timer = setTimeout(() => {
        timedOut = true
        ctrl.abort()
      }, timeoutMs)
    }

    try {
      const isForm = typeof FormData !== "undefined" && body instanceof FormData
      // 🔴 写操作即使没带 body 也照旧发一个 `{}`：老客户端一直是这么干的，
      //    后端有些路由（如 /api/workspaces/{name}/activate）就是被这么调的。
      //    别为了「干净」把它省掉——万一哪个端点声明了必填 body 就是一片 422。
      const noBody = body === undefined && (method === "GET" || method === "DELETE")
      const res = await fetch(path, {
        method,
        headers: isForm || noBody ? opts.headers : { "Content-Type": "application/json", ...opts.headers },
        body: isForm
          ? (body as FormData)
          : noBody
            ? undefined
            : JSON.stringify(body === undefined ? {} : body),
        signal: ctrl.signal,
        cache: "no-store",
      })

      if (res.ok) {
        if (res.status === 204) return undefined as T
        try {
          return (await res.json()) as T
        } catch {
          throw new ApiError("后端返回的不是 JSON", { status: res.status, kind: "parse" })
        }
      }

      const detail = await readErrorPayload(res)
      const serverSide = res.status >= 500
      const err = new ApiError(detail || httpMessage(res.status), {
        status: res.status,
        kind: serverSide ? "server" : "http",
        detail,
      })
      // 4xx 与 parse 都是「这一次就不会成功」的失败，直接抛，不浪费重试额度
      if (!serverSide) throw err
      lastErr = err
    } catch (err) {
      if (err instanceof ApiError) {
        if (!isTransient(err)) throw err
        lastErr = err
      } else if (signal?.aborted) {
        throw new ApiError("请求已取消", { kind: "aborted" })
      } else {
        lastErr = timedOut
          ? new ApiError(`后端 ${Math.round(timeoutMs / 1000)} 秒没回话，已放弃这次请求`, {
              kind: "timeout",
            })
          : new ApiError("连不上后端服务，确认一下 backend 有没有在跑", { kind: "network" })
      }
    } finally {
      if (timer !== undefined) clearTimeout(timer)
      if (signal) signal.removeEventListener("abort", forward)
    }

    if (attempt < attempts && lastErr) await sleep(260 * attempt * attempt)
  }

  throw lastErr ?? new ApiError("请求失败", { kind: "unknown" })
}

export const api = {
  get: <T>(path: string, opts?: RequestOpts) => request<T>(path, "GET", undefined, opts),
  post: <T>(path: string, body?: unknown, opts?: RequestOpts) =>
    request<T>(path, "POST", body, opts),
  patch: <T>(path: string, body?: unknown, opts?: RequestOpts) =>
    request<T>(path, "PATCH", body, opts),
  put: <T>(path: string, body?: unknown, opts?: RequestOpts) =>
    request<T>(path, "PUT", body, opts),
  del: <T>(path: string, opts?: RequestOpts) => request<T>(path, "DELETE", undefined, opts),
  /** multipart 上传：不要手写 Content-Type，boundary 得让浏览器自己生成 */
  upload: <T>(path: string, form: FormData, opts?: RequestOpts) =>
    request<T>(path, "POST", form, {
      ...opts,
      timeoutMs: opts?.timeoutMs ?? T_UPLOAD,
      attempts: 1,
    }),
}
