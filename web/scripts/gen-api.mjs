// 生成前后端契约：先让后端导出 openapi.json，再转成 TypeScript 类型。
// 用法：npm run gen:api
import { execFileSync } from "node:child_process"
import { existsSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const webDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const repoDir = path.resolve(webDir, "..")
const backendDir = path.join(repoDir, "backend")
const openapiJson = path.join(webDir, "src", "lib", "openapi.json")
const schemaDts = path.join(webDir, "src", "lib", "api-schema.d.ts")

function pythonExe() {
  if (process.env.PYTHON) return process.env.PYTHON
  const candidates =
    process.platform === "win32"
      ? [path.join(backendDir, ".venv", "Scripts", "python.exe")]
      : [path.join(backendDir, ".venv", "bin", "python")]
  const found = candidates.find((p) => existsSync(p))
  return found ?? (process.platform === "win32" ? "python" : "python3")
}

function openapiTypescriptCli() {
  return path.join(webDir, "node_modules", "openapi-typescript", "bin", "cli.js")
}

execFileSync(pythonExe(), [path.join(backendDir, "tools", "dump_openapi.py"), openapiJson], {
  cwd: backendDir,
  stdio: "inherit",
})

execFileSync(process.execPath, [openapiTypescriptCli(), openapiJson, "-o", schemaDts], {
  cwd: webDir,
  stdio: "inherit",
})

console.log(`schema -> ${schemaDts}`)
