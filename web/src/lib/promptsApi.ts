/**
 * P3 提示词工程接口：段列表 / 分镜中间层 / 三段式渲染 / 落盘。
 *
 * 三件事在这里收敛，页面里不再各写一遍：
 *   1) 分镜表的降级状态（READY / PARTIAL / NO_TABLE）翻译成人话；
 *   2) 渲染选项只在有覆盖值时才发，避免把继承来的风格又写回去；
 *   3) 保存撞名时区分「可以覆盖」与「文件名非法」。
 */
import { api, ApiError, type RequestOpts } from "./api.ts"

export const PROMPTS_KEY_ROOT = ["prompts"] as const

export type StoryboardStatus = "READY" | "PARTIAL" | "NO_TABLE"

export interface SegRange {
  start: number
  end: number
}

export interface SegmentFile {
  name: string
  kind: "ref" | "video" | "template" | "other"
  sub: string
  scene: string
}

export interface SegmentItem {
  id: string
  ep: number
  no: number
  title: string
  source_path: string
  range: SegRange | null
  duration: number
  shots: number
  spoken: number
  status: StoryboardStatus
  issues: number
  prompt_file: string
  /** 这一段在「提示词/」里关联的所有 md（参考图/视频/三段式/其他，按类型分类） */
  files: SegmentFile[]
}

export interface DialogueItem {
  who: string
  line: string
  lip: string
  kind: string
}

export interface Shot {
  no: string
  seq: number
  scene: string
  framing: string
  camera: string
  position: string
  movement: string
  focal: string
  duration: number
  start: number
  end: number
  content: string
  action: string
  dialogue_raw: string
  dialogue: DialogueItem[]
  sound: string
  transition: string
  light: string
  color: string
  props: string
  costume: string
  fx: string
  beat: string
  risk: string
  material: string
}

export interface Issue {
  kind: string
  field: string
  detail: string
}

export interface RiskRow {
  shots: string
  kind: string
  text: string
  level: string
  fix: string
}

export interface Storyboard {
  id: string
  ep: number
  no: number
  title: string
  source_path: string
  status: StoryboardStatus
  issues: Issue[]
  shots: Shot[]
  range: SegRange | null
  duration_target: number
  aspect: string
  resolution: string
  carry_in: string
  props_tracking: { prop: string; first: string; chain: string; end: string }[]
  risks: RiskRow[]
  shot_sum_sec?: number
}

export type MaterialSource = "auto" | "manual" | "missing" | "ignored"

export interface MaterialRow {
  placeholder: string
  name: string
  file: string
  note: string
  kind: "person" | "person_backup" | "scene" | "prop" | "audio"
  missing: boolean
  /** 这条是怎么来的：自动匹配 / 手动指定 / 待指定 / 已忽略 */
  source: MaterialSource
}

export interface PromptFile {
  path: string
  name: string
  dir: string
}

export interface BindingBody {
  placeholder: string
  file?: string
  ignored?: boolean
  name?: string
}

export interface BindingResult {
  segment: string
  placeholder: string
  binding: {
    placeholder: string
    file: string
    ignored: boolean
    name: string
    kind: string
    source: string
  } | null
  cleared: boolean
}

export const SOURCE_TEXT: Record<MaterialSource, string> = {
  auto: "自动",
  manual: "手动",
  missing: "待指定",
  ignored: "已忽略",
}

export interface Materials {
  rows: MaterialRow[]
  name_to_placeholder: Record<string, string>
  scene_name: string
  scene_time: string
  people: string[]
  props: string[]
  unmatched: MaterialRow[]
}

export interface InheritedContext {
  style: string
  scene: string
  era_region: string
  file: string
}

export interface SegmentDetail {
  storyboard: Storyboard
  materials: Materials
  context: InheritedContext
  size: { text: number; assets: number }
}

export interface RenderStats {
  shots: number
  duration: number
  spoken: number
  materials: number
  unbound: number
}

export interface Rendered {
  id: string
  file_stem: string
  seg_name: string
  title: string
  meta: string
  tables: { materials: string; timeline: string; dialogue: string }
  prefix: string
  body: string
  tail: string
  full: string
  issues: Issue[]
  stats: RenderStats
  source_path: string
  context: InheritedContext
  storyboard: Storyboard
  materials: Materials
}

export interface RenderOptions {
  style?: string
  scene?: string
  era_region?: string
  aspect?: string
  chunk_index?: number
}

export type IssueLevel = "error" | "warn" | "info"

export interface Finding {
  code: string
  level: IssueLevel
  title: string
  detail: string
  scope: string
  line: number
  shot: number
  fixable: boolean
  fix: { start: number; end: number; after: string } | null
}

export interface CheckResult {
  source: "render" | "draft"
  chars: number
  issues: Finding[]
  summary: { error: number; warn: number; info: number; fixable: number }
}

export interface PatchHunk {
  code: string
  title: string
  reason: string
  shot: number
  line: number
  start: number
  end: number
  before: string
  after: string
  context: string
}

export interface PatchResult {
  hunks: PatchHunk[]
  patched: string
  applied: number
  skipped: { code: string; title?: string; detail?: string; skip_reason?: string }[]
  issues_before: Finding[]
  issues_after: Finding[]
  summary_before: CheckResult["summary"]
  summary_after: CheckResult["summary"]
}

export const LEVEL_TEXT: Record<IssueLevel, string> = {
  error: "错误",
  warn: "提醒",
  info: "建议",
}

export interface SaveResult {
  path: string
  name: string
  bytes: number
}

export const STATUS_TEXT: Record<StoryboardStatus, string> = {
  READY: "已解析",
  PARTIAL: "部分解析",
  NO_TABLE: "未找到主表",
}

export async function listSegments(opts?: RequestOpts): Promise<SegmentItem[]> {
  const data = await api.get<{ segments: SegmentItem[] }>("/api/prompts/segments", opts)
  return data.segments ?? []
}

export function getSegment(seg: string, opts?: RequestOpts): Promise<SegmentDetail> {
  return api.get<SegmentDetail>(`/api/prompts/segments/${encodeURIComponent(seg)}`, opts)
}

export function getContext(opts?: RequestOpts): Promise<InheritedContext> {
  return api.get<InheritedContext>("/api/prompts/context", opts)
}

/** 只为「用户真的改过」的项发值：没改的不发，让后端继续继承项目风格。 */
export function compactOptions(raw: RenderOptions): RenderOptions {
  const out: RenderOptions = {}
  for (const [key, value] of Object.entries(raw)) {
    if (typeof value === "string") {
      const v = value.trim()
      if (v) out[key as keyof RenderOptions] = v as never
    } else if (typeof value === "number" && Number.isFinite(value)) {
      out[key as keyof RenderOptions] = value as never
    }
  }
  return out
}

export function renderSegment(
  seg: string,
  options: RenderOptions = {},
  opts?: RequestOpts
): Promise<Rendered> {
  return api.post<Rendered>(
    `/api/prompts/segments/${encodeURIComponent(seg)}/render`,
    compactOptions(options),
    opts
  )
}

export async function listFiles(
  kind: "image" | "audio",
  q = "",
  opts?: RequestOpts
): Promise<PromptFile[]> {
  const data = await api.get<{ files: PromptFile[] }>(
    `/api/prompts/files?kind=${kind}&q=${encodeURIComponent(q)}`,
    opts
  )
  return data.files ?? []
}

/** 人工指定 / 忽略 / 恢复自动。`file` 与 `ignored` 都不给 = 删记录回到自动。 */
export function setBinding(
  seg: string,
  body: BindingBody,
  opts?: RequestOpts
): Promise<BindingResult> {
  return api.put<BindingResult>(`/api/prompts/segments/${encodeURIComponent(seg)}/bindings`, body, opts)
}

export function checkPrompt(
  seg: string,
  content?: string,
  opts?: RequestOpts
): Promise<CheckResult> {
  return api.post<CheckResult>(
    `/api/prompts/segments/${encodeURIComponent(seg)}/check`,
    content == null ? {} : { content },
    opts
  )
}

/** 只改「可机械修」的地方；不传 codes 就是全修。 */
export function makePatch(
  seg: string,
  content: string,
  codes: string[] = [],
  opts?: RequestOpts
): Promise<PatchResult> {
  return api.post<PatchResult>(
    `/api/prompts/segments/${encodeURIComponent(seg)}/patch`,
    { content, codes },
    opts
  )
}

export async function savePrompt(
  seg: string,
  content: string,
  filename = "",
  overwrite = false,
  opts?: RequestOpts
): Promise<SaveResult> {
  return api.post<SaveResult>(
    `/api/prompts/segments/${encodeURIComponent(seg)}/save`,
    { content, filename, overwrite },
    opts
  )
}

/** 保存撞名（409）单独认出来：页面据此问一句「覆盖吗」，而不是报错了事。 */
export function isFileExists(e: unknown): boolean {
  return e instanceof ApiError && e.status === 409
}

/** 从整份 md 里取某一节的代码块（前缀 / 正文 / 尾部）。 */
export function blockOf(md: string, heading: string): string {
  const at = md.indexOf(heading)
  if (at < 0) return ""
  const rest = md.slice(at)
  const m = rest.match(/```[a-zA-Z]*\n([\s\S]*?)```/)
  return m ? m[1].trim() : ""
}

export interface MdSection {
  key: "prefix" | "body" | "tail"
  label: string
  heading: string
  hint: string
}

export const MD_SECTIONS: MdSection[] = [
  { key: "prefix", label: "前缀", heading: "## 前缀参考素材+风格+环境", hint: "风格 / 主体描述 / 场景环境，素材用 @ 占位" },
  { key: "body", label: "正文", heading: "## 正文", hint: "逐镜【时间轴】，一镜一段，不写时长" },
  { key: "tail", label: "尾部", heading: "## 尾部模板", hint: "镜头语言 / 光影 / 声音 / 防崩 / 合规 / 参考" },
]
