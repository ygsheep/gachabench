// 与后端接口一一对应的类型。字段命名保持 snake_case，避免来回映射出错。

export type CardStatus = "draft" | "final" | "reject"
export type MediaKind = "image" | "video" | "audio" | "other"

export interface Card {
  id: number
  workspace: string
  rel_path: string
  seg: string | null
  chunk: string | null
  status: CardStatus
  reject_tags: string[]
  reject_note: string
  tags: string[]
  note: string
  prompt_md: string | null
  split_plan_id: number | null
  /** 跑片当时的正文快照（详情接口才带全文，列表只有 has_prompt_snapshot） */
  has_prompt_snapshot?: boolean
  prompt_snapshot?: string | null
  /** 跑片当时用的参数（模型 / LoRA / 采样器 / 种子 …） */
  params?: Record<string, unknown> | null
  duration: number | null
  width: number | null
  height: number | null
  has_audio: number | null
  created_at: string
  updated_at: string
  /**
   * 文件已经不在盘上（被改名 / 移动 / 删掉）。
   * 记录仍然保留（标签、备注、废稿原因是手写的），只是不许骗人说它能播。
   */
  missing?: boolean
}

export interface CardsResp {
  cards: Card[]
  reject_tags: string[]
}

/**
 * 收编一轮的账（`POST /api/workspaces/{name}/intake`）。
 *
 * `copied` 才是真的复制进来的；`unclaimed` = 收进来了但认不出段号或块号
 * （原名保留、卡片标「未认领」）—— 这个数大于 0 时界面要提醒去抽卡台指定段号，
 * 否则点「通过」会 409。
 */
export interface IntakeStats {
  /** 参与这一轮的监听目录数（含不存在 / 被跳过的） */
  dirs: number
  /** 扫到的媒体文件数 */
  scanned: number
  copied: number
  skipped: number
  /** 复制时按模板改了名的条数 */
  renamed: number
  /** 认不出段号、原名保留的条数 */
  unclaimed: number
  problems: string[]
}

/** 五个内容角色的 key，与后端 FOLDER_KEYS 对齐 */
/** 内容角色 key，与后端 FOLDER_KEYS 对齐（`source` 暂不在设置页暴露）。
 *  asset = 所有素材的总库；material = 当前片段要用的备料区（按段分目录）。 */
export type FolderKey = "draft" | "final" | "prompt" | "storyboard" | "asset" | "material"

export interface Workspace {
  name: string
  root: string
  created_at: string
  /**
   * 显示名（用户可随时改，空串 = 用 name）。
   *
   * 🔴 name 是「目录名 + 数据库 workspace 键 + URL 段」，改它要搬盘、改库、重开连接；
   * 而 label 只是个标签。界面上要显示项目名，一律读 label，读写接口一律用 name。
   */
  label: string
  /** 每工作区的目录名覆盖；`asset: ""` 表示「资产目录未确定」 */
  folders: Record<string, string>
}

export interface InspectResp {
  root: string
  exists: boolean
  /** 根下一级子目录名（已跳过隐藏目录） */
  subdirs: string[]
  /** 自动匹配到的资产目录名，没命中为 null */
  asset_dir: string | null
  /** 内置候选名（供前端展示/手动选择） */
  asset_candidates: string[]
  /** 五个角色各自命中的已有目录名 */
  matched: Record<string, string>
  suggested_name: string
}

export interface AssetTargetResp {
  subject: string
  category: string
  /** 该主体将被放入的目录（相对资产根；空串 = 资产根） */
  dir: string
}

export interface Health {
  ok: boolean
  service: string
  version: string
  time: number
  ffmpeg: boolean
  comfyui: { base_url: string; configured: boolean }
  active_workspace: string | null
}

export interface Shot {
  index: number
  at_sec: number
  has_at: boolean
  raw: string
  head: string
  body: string
  /** 来自 §③bis 镜表，由 locate 接口补充 */
  duration?: number | null
  framing?: string | null
  camera?: string | null
}

export interface SplitChunk {
  chunk: string
  /** 旧版块 txt 路径。现在块正文按源 md 现算，不再落盘，这个字段可能为空。 */
  file?: string
  shots: number[]
  src_dur: number
  target_sec: number
  len_shots: number
}

/** 实时分块（GET /api/segments/{seg}/chunks）—— 块列表以它为准，不落盘、不过期 */
export interface SegmentChunksResp {
  seg: string
  strategy: string
  source_md: string
  source_total_sec: number
  chunks: SplitChunk[]
}

/** 单块正文（GET /api/segments/{seg}/block）—— 跑片与审片器读到的就是这一份 */
export interface SegmentBlockResp {
  seg: string
  chunk: string
  strategy: string
  source_md: string
  shots: number[]
  src_dur: number
  target_sec: number
  len_shots: number
  text: string
}

/**
 * 跑片参数元数据（GET /api/workflows）。
 * 后端按工作流节点自动探测，前端不写死任何模型/LoRA 名单。
 */
export interface WorkflowParam {
  key: string
  label: string
  role?: string
  type: "select" | "number" | "text"
  value: string | number | null
  options?: (string | number)[]
  min?: number | null
  max?: number | null
  step?: number | null
}

export interface WorkflowMeta {
  name: string
  rel: string
  params: WorkflowParam[]
  nodes: Record<string, string>
  error?: string
}

export interface SplitManifest {
  seg: string
  strategy: string
  source_md: string
  source_total_sec: number
  chunks: SplitChunk[]
  continuity: { carry_last_frame: boolean }
  created_at: string
}

export interface SplitResult {
  plan_id: number
  manifest: SplitManifest
}

/** 预览里某个参考文件在资产库中的匹配（后端已按精确/宽松匹配好） */
export interface PreviewPicAsset {
  found: boolean
  name: string | null
  rel: string | null
  fuzzy?: boolean
  /** 资产库原文件地址 —— 还没送进 ComfyUI input 也能预览 */
  url?: string | null
}

/**
 * 参考项状态。
 * - `ready`   已经在 ComfyUI input 目录里
 * - `auto`    资产库里匹配到了，跑片时后端自动送进去（用户不用点任何按钮）
 * - `missing` 两边都没有，必须人工补
 */
export type RefState = "ready" | "auto" | "missing"

/** 跑片前预览（GET /api/split/{plan_id}/preview）—— 参考图 / 音频是否就位 */
export interface PreviewPicItem {
  n: number
  filename: string
  kind?: string
  /** 是否已在 ComfyUI input 目录 */
  exists: boolean
  url: string | null
  asset?: PreviewPicAsset | null
  state?: RefState
}

export interface PreviewAudioItem {
  n: number
  /** 会接到的节点槽位：ref_audio_{slot}。后端按位置接线，跳号会把音色接给错的人 */
  slot?: number
  filename: string | null
  kind?: string
  exists: boolean
  url: string | null
  asset?: PreviewPicAsset | null
  state?: RefState
}

/** POST /api/split/{plan_id}/refs/select 的返回 */
export interface RefSelectResp {
  items: {
    n: number
    kind: string
    filename: string
    copied: boolean
    overwrote?: boolean
    source?: string
    fuzzy?: boolean
    reason?: string
  }[]
}

export interface SplitPreviewResp {
  plan_id: number
  seg: string
  chunk: string
  file: string
  strategy: string
  shots: number[]
  src_dur: number
  target_sec: number
  prompt_text: string
  pictures: PreviewPicItem[]
  audios: PreviewAudioItem[]
  /** <Audio N> 是否从 1 起连续 —— 不连续跑片会被后端拒掉（节点按位置接线，跳号接错音色） */
  audio_contiguous?: boolean
  input_dir: string
}

/** 参考图准备项（POST /api/split/{plan_id}/refs/from-asset 请求体 items[]） */
export interface RefPrepareItem {
  n: number
  filename: string
}

/** 参考图准备响应（from-asset / upload 共用）—— 只把文件放进 input_dir，不写入资产库 */
export interface RefPrepareResp {
  items: { n: number; filename: string; exists: boolean; copied: boolean; source: string | null }[]
}

/** locate 接口返回的命中镜块（行号 1-based，绝对行号） */
export interface LocatedShot {
  index: number
  start_line: number
  end_line: number
  raw: string
  head: string
  at_sec: number
  duration: number | null
  framing: string | null
  camera: string | null
}

export interface LocateResult {
  seg: string
  file: string
  file_abs: string
  parsed: { shots: number[]; dialogue: number[] }
  dialogue_map: Record<string, number[]>
  hits: LocatedShot[]
  missing: number[]
}

export interface AssetItem {
  rel: string
  name: string
  /** 所在目录（相对资产根；空串 = 资产根） */
  dir: string
  kind: MediaKind
  tags: string[]
  size: number
  url: string
}

export interface AssetGroup {
  name: string
  tags: string[]
  items: AssetItem[]
}

export interface AssetCategory {
  key: string
  label: string
  groups: AssetGroup[]
}

export interface AssetsResp {
  root: string
  /** 实际生效的资产目录名（可能是「资产」等别名，由工作区配置决定） */
  asset_dir: string
  total: number
  active_workspace: string | null
  categories: AssetCategory[]
}

export interface AssetUploadItem {
  rel: string
  name: string
  kind: MediaKind
  tags: string[]
  /** 落名后归到哪一套（文件名第一段） */
  subject: string
  /** 实际落盘目录（相对资产根） */
  dir: string
  size: number
  /** 目标名被占用、自动补了 `_02` 之类 */
  deduped: boolean
  url: string
}

export interface AssetUploadResp {
  ok: boolean
  /** 实际生效的资产目录名（素材 / 资产 …） */
  asset_dir: string
  /** 落盘的相对目录（空串 = 资产根） */
  dir: string
  /** 目录是后端自动定位出来的（而非前端指定） */
  auto_placed: boolean
  items: AssetUploadItem[]
  skipped: { name: string; reason: string }[]
}

export interface SegmentPrompt {
  seg: string
  title: string
  file: string
  sections: Record<string, string>
  codeblock: string
}

export interface SegmentsResp {
  segments: string[]
}

export interface SegmentsShotsResp {
  seg: string
  shots: Shot[]
}

export interface TicketResp {
  ticket: string
}

export type JobStatus = "queued" | "running" | "done" | "error" | "stopped"

/** 还在动的任务状态 —— 状态气泡、拆分台都按这个判「活动任务」 */
export const LIVE_JOB_STATUS = ["queued", "running"] as const

export interface JobStopResp {
  job_id: number
  status: JobStatus
  detail: string
}

export interface Job {
  id: number
  workspace: string
  seg: string | null
  chunk: string | null
  prompt_file: string | null
  comfy_prompt_id: string | null
  status: JobStatus
  stage: string | null
  progress: number | null
  log_tail: string | null
  created_at: string
  updated_at: string
}

export interface JobsResp {
  jobs: Job[]
}

/**
 * 原生文件夹选择器（GET /api/system/folder-picker）—— 能力探测。
 * `backend` 决定弹的是哪种框：`pwsh` 是 Win10 那个现代系统选择框，
 * `tkinter` 是回退的老式树形框。
 */
export interface FolderPickerCapability {
  available: boolean
  backend: "pwsh" | "tkinter" | null
}

/** POST /api/system/pick-folder 的返回 —— 用户取消时 path 为 null（不是错误） */
export interface PickFolderResp {
  path: string | null
  cancelled: boolean
  backend: string
}

/* ---------------------------------------------------------------------------
 * 配置（GET/PUT /api/config）
 * ------------------------------------------------------------------------- */

export interface ConfigComfy {
  base_url: string
  poll_interval_sec: number
  timeout_min: number
  comfy_output_dir: string
  input_dir: string
}

export interface ConfigFFmpeg {
  binary: string
  probe_binary: string
}

export interface ConfigWorkflow {
  json_path: string
  nodes: Record<string, unknown>
  ref_image_size: string
}

/** 卡池自动刷新（实时读盘）的节流参数，对应后端 `config.watch` */
export interface ConfigWatch {
  enabled: boolean
  /** 后台轮询间隔（秒）：只 stat 文件指纹，有新片才探测入库 */
  interval_sec: number
  /** 页面请求触发刷新的最小间隔（秒） */
  request_gap_sec: number
}

export interface ConfigResp {
  comfyui: ConfigComfy
  ffmpeg: ConfigFFmpeg
  folders: Record<FolderKey, string>
  asset_dir_candidates: string[]
  index_dir: string
  workflow: ConfigWorkflow
  workspaces: Workspace[]
  active: string | null
  watch?: ConfigWatch
}

/** GET /api/workflows 的一项（节点映射只读展示用） */
export type WorkflowListItem = WorkflowMeta

export interface WorkflowsResp {
  workflows: WorkflowListItem[]
  default: string
}

// ---------------------------------------------------------------------------
// 本地原著 / 剧本库（C2）
// ---------------------------------------------------------------------------

/** GET /api/library/books 的一项 */
export interface Book {
  id: number
  title: string
  author: string
  source: string
  dir_rel: string
  created_at: string
  chapters: number
  words: number
}

export interface ChapterRef {
  id: number
  idx: number
  title: string
  rel_path: string
  word_count: number
}

export interface BooksResp {
  books: Book[]
}

/** GET /api/library/books/{id} */
export interface BookDetail {
  id: number
  title: string
  author: string
  source: string
  dir_rel: string
  created_at: string
  chapters: ChapterRef[]
}

/** GET /api/library/chapters/{id}：正文已过 textclean 第一层（净化，不改写） */
export interface ChapterResp {
  id: number
  book_id: number
  book_title: string
  idx: number
  title: string
  rel_path: string
  text: string
  words: number
}

/** POST /api/library/chapters/{id}/excerpt */
export interface ExcerptResp {
  text: string
  words: number
  from: number
  to: number
}

/** POST /api/library/import */
export interface ImportResp {
  book_id: number | null
  title: string
  dir_rel: string
  source: string
  chapters: ChapterRef[]
  skipped: string[]
}

// ---------------------------------------------------------------- 联网采集（C3）

/** GET /api/search/status —— 未启用也返回 200 */
export interface SearchStatus {
  enabled: boolean
  enabled_flag: boolean
  disclaimer_acked: boolean
  disclaimer: string
  sources_path: string
  sources_total: number
  sources_usable: number
  errors: string[]
  attribution: boolean
  timeout_sec: number
}

/** GET /api/search/sources */
export interface SearchSource {
  name: string
  url: string
  can_search: boolean
  can_toc: boolean
  can_content: boolean
  /** 非空 = 这个源跑不了；里面是人话原因，直接显示给用户 */
  unsupported: string
}

/** POST /api/search —— 多源聚合后的一个候选源定位 */
export interface SearchCandidate {
  source: string
  bookUrl: string
}

/** POST /api/search —— 一条命中（可能是多源聚合后的） */
export interface SearchHit {
  name: string
  author: string
  bookUrl: string
  cover: string
  intro: string
  lastChapter: string
  kind?: string
  wordCount?: string
  /** 这本书在哪些源里找到：多个源 = 可以换源取目录/正文 */
  candidates?: SearchCandidate[]
  sources?: string[]
}

/**
 * 一次搜索的书源账本。
 *
 * 没有它，「为什么只有 3 个源有结果」永远说不清是超时放弃、请求失败，
 * 还是源站本身没收录 —— 用户会以为是软件坏了。
 */
export interface SearchStats {
  requested: number
  returned: number
  ok: number
  empty: number
  failed: number
  abandoned: number
  hit: number
  elapsed: number
  workers: number
}

export interface SearchResp {
  keyword: string
  source: string
  /** "all" = 全网多源并行；"single" = 指定书源 */
  mode: "all" | "single"
  books: SearchHit[]
  stats?: SearchStats
}

/** POST /api/search/toc */
export interface TocResp {
  source: string
  book_url: string
  chapters: { title: string; url: string }[]
}

/** POST /api/search/chapter */
export interface ChapterTextResp {
  title: string
  text: string
}

/** POST /api/search/import —— 比 ImportResp 多两字段 */
export interface CollectResp extends ImportResp {
  /** 取失败的章节，带原因（不整本放弃） */
  failed: { index: number; title: string; reason: string }[]
  requested: number
}

// ---------------------------------------------------------------------------
// P1 资产索引（GET/PATCH /api/assets …）
// ---------------------------------------------------------------------------

/** 索引里的一个资产（列表 / 详情共用的一层） */
export interface IndexAssetItem {
  id: string
  path: string
  name: string
  dir_path: string
  role: string
  subject: string
  category: string
  ext: string
  size: number
  mtime: number
  sha256: string | null
  hash_status: string
  media_kind: string
  width: number | null
  height: number | null
  status: string
  tags: string[]
  aliases: string[]
  thumb_url: string | null
  media_url: string
  /** 镜头类的段号（`17.1_成片.mp4` → "17"）；素材类为 null。
   *  镜头类没有主体（位置即身份），分组时按这个字段落「段 17」。 */
  seg?: string | null
}
export interface IndexRef {
  id: string
  file_path: string
  line: number | null
  col: number | null
  kind: string
  segment: string | null
}

export interface IndexAssetDetail extends IndexAssetItem {
  note: string
  subject_override: string | null
  refs: IndexRef[]
}

export interface IndexAssetListResp {
  items: IndexAssetItem[]
  next_cursor: string | null
  total: number
  root: string
  workspace: string
}

export interface IndexRoleCount {
  key: string
  label: string
  count: number
}

export interface IndexCollectionCount {
  id: string
  name: string
  kind: string
  builtin: boolean
  count: number
}

export interface IndexCollectionsResp {
  roles: IndexRoleCount[]
  collections: IndexCollectionCount[]
}

export interface AssetScanStartResp {
  task_id: string
  status: string
}

export interface AssetScanStatusResp {
  id: string
  status: string
  started_at: string
  finished_at: string | null
  stats: Record<string, unknown>
  force: boolean
}

export interface IndexMetaPatch {
  tags?: string[]
  aliases?: string[]
  note?: string
  subject_override?: string
}

/** SSE `index.progress` 的 data */
export interface AssetProgressEvent {
  task_id: string
  workspace: string
  processed: number
  total: number
  done: boolean
  status?: string
}

/** SSE `file.changed` 的 data */
export interface AssetChangedEvent {
  task_id: string
  workspace: string
  status: string
  stats: Record<string, unknown>
}

// ---------------------------------------------------------------------------
// P2 文件操作 / 回收站 / 重复文件
// ---------------------------------------------------------------------------

/** 计划类型：改名 / 移动 / 删除 */
export type AssetOpsKind = "rename" | "move" | "delete"

/** POST /api/assets/operations/plan 的请求体（字段按 kind 取） */
export interface OpsPlanBody {
  kind: AssetOpsKind
  asset_id?: string
  new_name?: string
  asset_ids?: string[]
  dest_dir?: string
}

export interface OpsPlanStats {
  assets: number
  files: number
  refs: number
}

/** 计划里一个将被改写的引用文件 */
export interface OpsPlanFile {
  file_path: string
  line: number | null
  kind: string
  before: string
  after: string
  selected: boolean
  encoding: string
  refs: number
}

/** 计划里不可执行的资产（例如目标名冲突） */
export interface OpsPlanBlocked {
  asset_id: string
  path: string
  reason: string
}

export interface OpsMoveResult {
  asset_id: string
  from: string
  to: string
  sha256: string | null
  size: number
  mtime: number
}

export interface OpsTrashResult {
  asset_id: string
  from: string
  to: string
}

export interface OpsTextResult {
  file_path: string
  backup_rel: string
  sha256: string | null
  size: number
  mtime: number
}

/** apply 之后 result 里的实际动作清单 */
export interface OpsApplyResult {
  moves: OpsMoveResult[]
  trashed: OpsTrashResult[]
  texts: OpsTextResult[]
  stats: OpsPlanStats
}

export interface OpsPlanPayload {
  asset_id?: string
  old_name?: string
  new_name?: string
  asset_ids?: string[]
  dest_dir?: string
}

/** 计划接口（plan / apply / rollback / detail）的统一返回 */
export interface OpsPlan {
  id: string
  kind: AssetOpsKind
  status: string
  created_at: string
  expires_at: string
  applied_at: string | null
  payload: OpsPlanPayload
  stats: OpsPlanStats
  files: OpsPlanFile[]
  blocked: OpsPlanBlocked[]
  result: OpsApplyResult | null
}

/** GET /api/trash 的一项（以 backend/modules/assets/trash.py 的实际返回为准） */
export interface TrashEntry {
  id: string
  asset_id: string
  name: string
  original_path: string
  trash_path: string
  deleted_at: string
  size: number
  ext: string
  media_kind: string
  sha256: string | null
}

export interface TrashListResp {
  items: TrashEntry[]
  total: number
}

export interface TrashRestoreResp {
  ok: boolean
  id: string
  asset_id: string
  path: string
  name: string
  status: string
}

export interface TrashPurgeResp {
  ok: boolean
  id: string
  asset_id: string
}

/** GET /api/assets/duplicates 的一组（sha256 相同） */
export interface DuplicateGroup {
  sha256: string
  count: number
  size: number
  items: IndexAssetItem[]
}

export interface DuplicatesResp {
  groups: DuplicateGroup[]
  total: number
}

/** GET /api/assets/unreferenced */
export interface UnreferencedResp {
  items: IndexAssetItem[]
  total: number
}
