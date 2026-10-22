import createClient from "openapi-fetch"
import type { paths } from "./api-schema"

/** 后端地址：开发态直连 8000，打包态由 VITE_API_BASE 或同源地址决定。 */
export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000"

export const client = createClient<paths>({ baseUrl: API_BASE })
