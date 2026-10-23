import { useEffect, useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  FileDown,
  RefreshCw,
  SlidersHorizontal,
  TriangleAlert,
  Wrench,
  X,
} from "lucide-react"
import { toast } from "sonner"
import { cn } from "cn"
import { ApiError } from "@/lib/api"
import {
  MD_SECTIONS,
  PROMPTS_KEY_ROOT,
  SOURCE_TEXT,
  STATUS_TEXT,
  LEVEL_TEXT,
  blockOf,
  checkPrompt,
  getSegment,
  isFileExists,
  listFiles,
  listSegments,
  makePatch,
  renderSegment,
  savePrompt,
  setBinding,
  type BindingBody,
  type CheckResult,
  type Finding,
  type MaterialRow,
  type PatchResult,
  type SegmentFile,
  type SegmentItem,
  type Shot,
} from "@/lib/promptsApi"

/* 提示词工程：左边选段 → 中间看分镜表 → 右边出三段式。
 *
 * 三栏各自只回答一个问题：
 *   左：这段有没有分镜、缺不缺件；
 *   中：分镜表里到底写了什么（只读，真相在 md 里）；
 *   右：按三段式重写出来长什么样、能不能落盘。
 * 中间不放编辑框、右边不放镜表：一改就会「显示新的、存的旧的」。
 */

type RightTab = "prefix" | "body" | "tail" | "full" | "check"

export function PromptStudio({
  seg,
  onSegChange,
}: {
  seg?: string
  onSegChange?: (seg: string) => void
}) {
  const qc = useQueryClient()
  const [picked, setPicked] = useState("")
  const [midTab, setMidTab] = useState<"shots" | "materials" | "issues">("shots")
  const [rightTab, setRightTab] = useState<RightTab>("prefix")
  /* 单独看一镜：表格再宽也塞不下 21 个字段的长句，点行开抽屉看全文 */
  const [openShot, setOpenShot] = useState<number | null>(null)
  /* 正在给哪个占位挑文件：图还是音频，决定候选清单 */
  const [picker, setPicker] = useState<{
    placeholder: string
    kind: "image" | "audio"
    name: string
  } | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [era, setEra] = useState("")
  const [styleText, setStyleText] = useState("")
  const [sceneText, setSceneText] = useState("")
  /* 草稿 = 「服务端那一份 + 用户改过的字」。用 base 记住改的是哪一版：
     后端重渲染出新版本时 base 对不上，自动回落到新版本，不用 effect 去覆盖。 */
  const [edited, setEdited] = useState<{ base: string; text: string } | null>(null)

  const segsQ = useQuery({
    queryKey: [...PROMPTS_KEY_ROOT, "segments"],
    queryFn: () => listSegments(),
    retry: 1,
  })
  const segments = useMemo(() => segsQ.data ?? [], [segsQ.data])
  const cur = seg && segments.some((s) => s.id === seg) ? seg : picked || segments[0]?.id || ""

  const detailQ = useQuery({
    queryKey: [...PROMPTS_KEY_ROOT, "detail", cur],
    queryFn: () => getSegment(cur),
    enabled: !!cur,
    retry: 1,
  })

  const renderQ = useQuery({
    queryKey: [...PROMPTS_KEY_ROOT, "render", cur, era, styleText, sceneText],
    queryFn: () =>
      renderSegment(cur, {
        era_region: era,
        style: styleText,
        scene: sceneText,
      }),
    enabled: !!cur,
    retry: 1,
  })
  const rendered = renderQ.data

  const serverFull = rendered?.full ?? ""
  const draft = edited && edited.base === serverFull ? edited.text : serverFull
  const dirty = !!edited && edited.base === serverFull && edited.text !== serverFull
  const setDraft = (text: string) => setEdited({ base: serverFull, text })

  const save = useMutation({
    mutationFn: (overwrite: boolean) => savePrompt(cur, draft, "", overwrite),
    onSuccess: (res) => {
      setEdited(null)
      toast.success(`已写入 ${res.path}`)
      void qc.invalidateQueries({ queryKey: [...PROMPTS_KEY_ROOT, "segments"] })
    },
  })

  const checkM = useMutation({
    mutationFn: (content: string) => checkPrompt(cur, content),
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  })
  const patchM = useMutation({
    mutationFn: (content: string) => makePatch(cur, content),
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  })

  function runCheck(text: string) {
    if (!cur || !text) return
    checkM.mutate(text)
  }

  function applyPatch() {
    if (!patchM.data) return
    setDraft(patchM.data.patched)
    toast.success(`已应用 ${patchM.data.applied} 处改动，接着记得保存`)
    checkM.mutate(patchM.data.patched)
    patchM.reset()
  }

  const bind = useMutation({
    mutationFn: (body: BindingBody) => setBinding(cur, body),
    onSuccess: (res) => {
      const verb = res.cleared ? "已恢复自动" : res.binding?.ignored ? "已忽略" : "已指定"
      toast.success(`${res.placeholder} ${verb}`)
      void qc.invalidateQueries({ queryKey: [...PROMPTS_KEY_ROOT, "detail", cur] })
      void qc.invalidateQueries({ queryKey: [...PROMPTS_KEY_ROOT, "render", cur] })
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  })

  async function handleSave() {
    if (!cur || !draft) return
    try {
      await save.mutateAsync(false)
    } catch (e) {
      if (isFileExists(e)) {
        if (window.confirm("同名文件已存在，覆盖它？原文件不会进回收站。")) {
          try {
            await save.mutateAsync(true)
          } catch (err) {
            toast.error(err instanceof Error ? err.message : String(err))
          }
        }
        return
      }
      const msg = e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e)
      toast.error(msg)
    }
  }

  async function copy(text: string, label: string) {
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      toast.success(`${label}已复制`)
    } catch {
      toast.error("剪贴板不可用，手动全选复制吧")
    }
  }

  const shots = detailQ.data?.storyboard.shots ?? []
  const issues = rendered?.issues ?? detailQ.data?.storyboard.issues ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex flex-wrap items-end gap-x-3.5 gap-y-2 px-6 pb-3.5 pt-5">
        <div className="max-w-[86ch]">
          <h1 className="text-[19px] font-semibold leading-tight">提示词工程</h1>
          <p className="pt-0.5 text-[12.5px] text-ash">
            分镜表是真相，这里只做投影与重写：按 Seedance 三段式重排成「前缀 / 正文 / 尾部」，
            看一眼再落盘，不改分镜源文件。
          </p>
        </div>
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => setSettingsOpen((v) => !v)}
          className={cn(
            "inline-flex h-8 items-center gap-1.5 rounded-ctl border px-3 text-[12px] transition-colors",
            settingsOpen
              ? "border-wash-line bg-wash text-paper"
              : "border-rule bg-cell text-ash hover:bg-hi hover:text-paper"
          )}
        >
          <SlidersHorizontal className="size-3.5" />
          渲染设定
        </button>
        <button
          type="button"
          onClick={() => void renderQ.refetch()}
          className="inline-flex h-8 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-3 text-[12px] text-ash transition-colors hover:bg-hi hover:text-paper active:translate-y-px"
        >
          <RefreshCw className={cn("size-3.5", renderQ.isFetching && "animate-spin")} />
          重新渲染
        </button>
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={!draft || save.isPending}
          className="inline-flex h-8 items-center gap-1.5 rounded-ctl border border-wash-line bg-wash px-3 text-[12px] font-medium text-paper transition-colors hover:bg-hi disabled:opacity-45"
        >
          <FileDown className="size-3.5" />
          {save.isPending ? "写入中…" : "保存为提示词"}
        </button>
      </header>

      {settingsOpen && (
        <section className="mx-6 mb-3 grid gap-3 rounded-panel border border-rule-soft bg-panel p-3.5">
          <div className="text-[11.5px] text-ash">
            留空 = 继承项目里已经定下来的那份。当前继承自{" "}
            <span className="font-mono text-paper">{rendered?.context.file || "（没有可继承的提示词）"}</span>
          </div>
          <label className="grid gap-1">
            <span className="text-[11px] text-ash-dim">年代 / 地域</span>
            <input
              value={era}
              onChange={(e) => setEra(e.target.value)}
              placeholder={rendered?.context.era_region || "如 1985年中国苏北农村"}
              className="h-8 rounded-ctl border border-rule bg-cell px-2.5 text-[12px] outline-none placeholder:text-ash-dim focus:border-wash-line"
            />
          </label>
          <label className="grid gap-1">
            <span className="text-[11px] text-ash-dim">【风格】整段（改了就整段替换）</span>
            <textarea
              rows={3}
              value={styleText}
              onChange={(e) => setStyleText(e.target.value)}
              placeholder={rendered?.context.style || "留空则用模板默认风格"}
              className="resize-y rounded-ctl border border-rule bg-cell px-2.5 py-2 font-mono text-[11.5px] leading-relaxed outline-none placeholder:text-ash-dim focus:border-wash-line"
            />
          </label>
          <label className="grid gap-1">
            <span className="text-[11px] text-ash-dim">【场景环境】整段</span>
            <textarea
              rows={3}
              value={sceneText}
              onChange={(e) => setSceneText(e.target.value)}
              placeholder={rendered?.context.scene || "留空则按场次与素材自动生成"}
              className="resize-y rounded-ctl border border-rule bg-cell px-2.5 py-2 font-mono text-[11.5px] leading-relaxed outline-none placeholder:text-ash-dim focus:border-wash-line"
            />
          </label>
        </section>
      )}

      <div className="flex min-h-0 flex-1 border-t border-rule-soft max-[1180px]:flex-col">
        <SegmentRail
          segments={segments}
          cur={cur}
          pending={segsQ.isPending}
          onPick={(id) => {
            setPicked(id)
            onSegChange?.(id)
          }}
        />

        <section className="flex min-h-0 min-w-0 flex-1 flex-col max-[1180px]:border-t max-[1180px]:border-rule-soft">
          <div className="flex items-center gap-1 border-b border-rule-soft px-4">
            {(
              [
                ["shots", `分镜表 ${shots.length}`],
                ["materials", `素材 ${detailQ.data?.materials.rows.length ?? 0}`],
                ["issues", `问题 ${issues.length}`],
              ] as ["shots" | "materials" | "issues", string][]
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setMidTab(key)}
                aria-pressed={midTab === key}
                className={cn(
                  "relative h-9 px-2.5 text-[12px] transition-colors",
                  midTab === key ? "font-medium text-paper" : "text-ash hover:text-paper"
                )}
              >
                {label}
                {midTab === key && <span className="absolute inset-x-2 bottom-0 h-px bg-paper" />}
              </button>
            ))}
            <span className="flex-1" />
            {midTab === "shots" && shots.length > 0 && (
              <span className="whitespace-nowrap pr-3 text-[10.5px] text-ash-dim">
                点任意一行看全字段
              </span>
            )}
            {detailQ.data?.storyboard.source_path && (
              <span className="truncate font-mono text-[10.5px] text-ash-dim">
                {detailQ.data.storyboard.source_path}
              </span>
            )}
          </div>

          <div className="min-h-0 flex-1 overflow-auto">
            {!cur && !segsQ.isPending && (
              <Empty
                title="没有扫到分镜脚本"
                body="把 21 字段的分镜 md 放进工作区的 分镜/ 或 分镜表/ 目录，文件名带「第N集」「第N段」即可。"
              />
            )}
            {cur && detailQ.isPending && <Empty title="解析分镜表…" body="按列名建映射，不按列序猜。" />}
            {cur && detailQ.isError && (
              <Empty title="这一段读不出来" body={(detailQ.error as Error)?.message ?? "后端返回了错误"} />
            )}
            {midTab === "shots" && detailQ.data && <ShotTable shots={shots} onOpen={setOpenShot} />}
            {midTab === "materials" && detailQ.data && (
              <MaterialTable
                rows={detailQ.data.materials.rows}
                scene={detailQ.data.materials.scene_name}
                busy={bind.isPending}
                onPick={(row) =>
                  setPicker({
                    placeholder: row.placeholder,
                    kind: row.kind === "audio" ? "audio" : "image",
                    name: row.name,
                  })
                }
                onIgnore={(row) =>
                  bind.mutate({ placeholder: row.placeholder, ignored: true, name: row.name })
                }
                onReset={(row) => bind.mutate({ placeholder: row.placeholder })}
              />
            )}
            {midTab === "issues" && <IssueList issues={issues} />}
          </div>
        </section>

        <aside className="flex w-[440px] min-w-[440px] flex-col border-l border-rule-soft max-[1180px]:w-full max-[1180px]:min-w-0 max-[1180px]:border-l-0 max-[1180px]:border-t">
          <div className="flex items-center gap-1 border-b border-rule-soft px-3">
            {MD_SECTIONS.map((s) => (
              <button
                key={s.key}
                type="button"
                onClick={() => setRightTab(s.key)}
                aria-pressed={rightTab === s.key}
                className={cn(
                  "relative h-9 px-2.5 text-[12px] transition-colors",
                  rightTab === s.key ? "font-medium text-paper" : "text-ash hover:text-paper"
                )}
              >
                {s.label}
                {rightTab === s.key && <span className="absolute inset-x-2 bottom-0 h-px bg-paper" />}
              </button>
            ))}
            <button
              type="button"
              onClick={() => setRightTab("full")}
              aria-pressed={rightTab === "full"}
              className={cn(
                "relative h-9 px-2.5 text-[12px] transition-colors",
                rightTab === "full" ? "font-medium text-paper" : "text-ash hover:text-paper"
              )}
            >
              全文
              {rightTab === "full" && <span className="absolute inset-x-2 bottom-0 h-px bg-paper" />}
            </button>
            <button
              type="button"
              onClick={() => {
                setRightTab("check")
                if (draft) runCheck(draft)
              }}
              aria-pressed={rightTab === "check"}
              className={cn(
                "relative flex h-9 items-center gap-1 px-2.5 text-[12px] transition-colors",
                rightTab === "check" ? "font-medium text-paper" : "text-ash hover:text-paper"
              )}
            >
              检查
              {!!checkM.data?.summary && checkM.data.summary.error > 0 && (
                <span className="rounded border border-wash-line px-1 text-[9.5px] leading-[15px] text-paper">
                  {checkM.data.summary.error}
                </span>
              )}
              {rightTab === "check" && <span className="absolute inset-x-2 bottom-0 h-px bg-paper" />}
            </button>
            <span className="flex-1" />
            {dirty && <span className="pr-1 text-[10.5px] text-ash-dim">未保存</span>}
          </div>

          {renderQ.isError && (
            <div className="m-3 rounded-ctl border border-rule bg-cell p-3 text-[12px] text-ash">
              {(renderQ.error as Error)?.message ?? "渲染失败"}
            </div>
          )}

          {rightTab === "check" ? (
            <CheckPane
              result={checkM.data}
              pending={checkM.isPending}
              error={checkM.isError ? (checkM.error as Error)?.message : ""}
              patch={patchM.data}
              patching={patchM.isPending}
              onRun={() => runCheck(draft)}
              onFix={() => patchM.mutate(draft)}
              onApply={applyPatch}
              onDiscard={() => patchM.reset()}
            />
          ) : rightTab === "full" ? (
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              spellCheck={false}
              className="min-h-0 flex-1 resize-none bg-panel px-3.5 py-3 font-mono text-[11.5px] leading-[1.75] text-paper outline-none"
            />
          ) : (
            <BlockView
              text={draft ? blockOf(draft, headingOf(rightTab)) : ""}
              hint={MD_SECTIONS.find((s) => s.key === rightTab)?.hint ?? ""}
              onCopy={() => void copy(blockOf(draft, headingOf(rightTab)), labelOf(rightTab))}
            />
          )}

          <footer className="flex items-center gap-2 border-t border-rule-soft px-3 py-2">
            <span className="flex-1 truncate font-mono text-[10.5px] text-ash-dim">
              {rendered ? `${rendered.file_stem}_三段式.md` : "—"}
            </span>
            <button
              type="button"
              onClick={() => void copy(draft, "全文")}
              className="inline-flex h-7 items-center gap-1 rounded-ctl border border-rule bg-cell px-2 text-[11px] text-ash transition-colors hover:bg-hi hover:text-paper"
            >
              <Copy className="size-3" />
              复制全文
            </button>
          </footer>
        </aside>
      </div>

      {picker && (
        <FilePicker
          kind={picker.kind}
          label={`${picker.placeholder} · ${picker.name}`}
          onClose={() => setPicker(null)}
          onPick={(path) => {
            bind.mutate({ placeholder: picker.placeholder, file: path, name: picker.name })
            setPicker(null)
          }}
        />
      )}

      {openShot !== null && shots[openShot] && (
        <ShotSheet
          shot={shots[openShot]}
          index={openShot}
          total={shots.length}
          onClose={() => setOpenShot(null)}
          onPrev={() => setOpenShot((i) => (i === null ? null : Math.max(0, i - 1)))}
          onNext={() =>
            setOpenShot((i) => (i === null ? null : Math.min(shots.length - 1, i + 1)))
          }
        />
      )}
    </div>
  )
}

function CheckPane({
  result,
  pending,
  error,
  patch,
  patching,
  onRun,
  onFix,
  onApply,
  onDiscard,
}: {
  result?: CheckResult
  pending: boolean
  error: string
  patch?: PatchResult
  patching: boolean
  onRun: () => void
  onFix: () => void
  onApply: () => void
  onDiscard: () => void
}) {
  const summary = result?.summary
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-rule-soft/60 px-3 py-1.5">
        <span className="flex-1 text-[10.5px] text-ash-dim">
          {summary
            ? `${summary.error} 错误 · ${summary.warn} 提醒 · ${summary.info} 建议`
            : "按模板硬规矩逐条量一遍，不改稿"}
        </span>
        <button
          type="button"
          onClick={onRun}
          disabled={pending}
          className="inline-flex h-6 items-center gap-1 rounded-ctl border border-rule bg-cell px-1.5 text-[10.5px] text-ash transition-colors hover:bg-hi hover:text-paper disabled:opacity-40"
        >
          <RefreshCw className={cn("size-3", pending && "animate-spin")} />
          重新检查
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-3 py-2">
        {error && <p className="pb-2 text-[11.5px] text-ash">{error}</p>}
        {pending && !result && <p className="text-[12px] text-ash">检查中…</p>}
        {!pending && !result && !error && (
          <p className="text-[12px] text-ash">还没有结果。点右上角「重新检查」跑一次。</p>
        )}

        {patch && (
          <div className="mb-3 rounded-ctl border border-wash-line bg-cell p-2.5">
            <div className="flex items-center gap-2 pb-1.5">
              <span className="flex-1 text-[11.5px] text-paper">
                补丁预览：{patch.applied} 处
              </span>
              <button
                type="button"
                onClick={onApply}
                className="h-6 rounded-ctl border border-wash-line bg-wash px-2 text-[10.5px] font-medium text-paper hover:bg-hi"
              >
                应用到草稿
              </button>
              <button
                type="button"
                onClick={onDiscard}
                className="h-6 rounded-ctl border border-rule bg-cell px-2 text-[10.5px] text-ash hover:bg-hi hover:text-paper"
              >
                放弃
              </button>
            </div>
            <ul className="flex flex-col gap-1.5">
              {patch.hunks.map((h, i) => (
                <li key={`${h.code}-${i}`} className="rounded border border-rule-soft/70 px-2 py-1.5">
                  <div className="text-[10px] text-ash-dim">
                    第 {h.line} 行 · {h.title}
                  </div>
                  <div className="break-all font-mono text-[11px] text-ash-dim line-through">
                    {h.before || "（空）"}
                  </div>
                  <div className="break-all font-mono text-[11px] text-paper">
                    {h.after || "（删除）"}
                  </div>
                </li>
              ))}
              {patch.hunks.length === 0 && (
                <li className="text-[11px] text-ash">这次没有可自动修的地方，剩下的要手改。</li>
              )}
            </ul>
          </div>
        )}

        {result && result.issues.length === 0 && (
          <p className="flex items-center gap-1.5 py-2 text-[12px] text-ash">
            <Check className="size-3.5" />
            这一段没查出问题：三节齐、占位与素材表对得上、台词与分镜表一致。
          </p>
        )}

        <ul className="flex flex-col gap-1.5">
          {result?.issues.map((f: Finding, i) => (
            <li
              key={`${f.code}-${i}`}
              className="rounded-ctl border border-rule-soft/70 bg-panel px-2.5 py-1.5"
            >
              <div className="flex items-center gap-1.5">
                <span
                  className={cn(
                    "rounded border px-1 text-[9.5px] leading-[15px]",
                    f.level === "error"
                      ? "border-wash-line bg-wash text-paper"
                      : f.level === "warn"
                        ? "border-rule text-ash"
                        : "border-transparent text-ash-dim"
                  )}
                >
                  {LEVEL_TEXT[f.level]}
                </span>
                <span className="flex-1 text-[11.5px] text-paper">{f.title}</span>
                {f.line > 0 && (
                  <span className="font-mono text-[9.5px] text-ash-dim">L{f.line}</span>
                )}
              </div>
              <p className="pt-1 text-[11px] leading-relaxed text-ash">{f.detail}</p>
              {f.fixable && <p className="pt-0.5 text-[10px] text-ash-dim">可自动修</p>}
            </li>
          ))}
        </ul>
      </div>

      <footer className="flex items-center gap-2 border-t border-rule-soft px-3 py-2">
        <span className="flex-1 text-[10.5px] text-ash-dim">
          {summary && summary.fixable > 0
            ? `${summary.fixable} 处可自动修，只动命中区间，台词不碰`
            : "没有可自动修的问题"}
        </span>
        <button
          type="button"
          onClick={onFix}
          disabled={patching || !summary || summary.fixable === 0}
          className="inline-flex h-7 items-center gap-1 rounded-ctl border border-rule bg-cell px-2 text-[11px] text-ash transition-colors hover:bg-hi hover:text-paper disabled:opacity-40"
        >
          <Wrench className="size-3" />
          生成修复补丁
        </button>
      </footer>
    </div>
  )
}

function headingOf(key: "prefix" | "body" | "tail"): string {
  return MD_SECTIONS.find((s) => s.key === key)?.heading ?? ""
}

function labelOf(key: "prefix" | "body" | "tail"): string {
  return MD_SECTIONS.find((s) => s.key === key)?.label ?? ""
}

function SegmentRail({
  segments,
  cur,
  pending,
  onPick,
}: {
  segments: SegmentItem[]
  cur: string
  pending: boolean
  onPick: (id: string) => void
}) {
  const byEp = useMemo(() => {
    const map = new Map<number, SegmentItem[]>()
    for (const s of segments) {
      const list = map.get(s.ep) ?? []
      list.push(s)
      map.set(s.ep, list)
    }
    return [...map.entries()].sort((a, b) => a[0] - b[0])
  }, [segments])

  return (
    <aside className="flex w-[252px] min-w-[252px] flex-col max-[1180px]:w-full max-[1180px]:min-w-0">
      <div className="flex items-center justify-between px-4 pb-1.5 pt-3 text-[11px] text-ash-dim">
        <span>分镜段</span>
        <span className="font-mono">{segments.length}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-2 pb-6">
        {pending && <p className="px-2 py-2 text-[12px] text-ash">读段列表…</p>}
        {byEp.map(([ep, list]) => (
          <div key={ep} className="mb-2">
            <div className="px-2 py-1 text-[10.5px] uppercase tracking-wide text-ash-dim">
              第 {ep} 集
            </div>
            {list.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => onPick(s.id)}
                aria-current={s.id === cur ? "true" : undefined}
                className={cn(
                  "mb-1 flex w-full flex-col gap-1 rounded-ctl border px-2.5 py-2 text-left transition-colors",
                  s.id === cur
                    ? "border-wash-line bg-wash"
                    : "border-transparent hover:border-rule hover:bg-cell"
                )}
              >
                <span className="flex items-baseline gap-1.5">
                  <span className="font-mono text-[13px] font-medium text-paper">
                    {String(s.no).padStart(2, "0")}
                  </span>
                  <span className="text-[11px] text-ash">第 {s.no} 段</span>
                  <span className="flex-1" />
                  <span
                    className={cn(
                      "rounded border px-1 text-[9.5px] leading-[15px]",
                      s.status === "READY"
                        ? "border-rule text-ash"
                        : "border-wash-line text-paper"
                    )}
                  >
                    {STATUS_TEXT[s.status] ?? s.status}
                  </span>
                </span>
                <span className="flex items-center gap-2 text-[10.5px] text-ash-dim">
                  <span className="font-mono">{s.duration.toFixed(1)}s</span>
                  <span>{s.shots} 镜</span>
                  {s.spoken > 0 && <span>{s.spoken} 发声</span>}
                  {s.prompt_file && <span className="text-ash">已有稿</span>}
                </span>
                {/* 关联文件：按场景配对参考图↔视频提示词 */}
                {s.files && s.files.length > 0 && (
                  <span className="flex flex-col gap-0.5">
                    {pairFiles(s.files).map((p) => (
                      <span
                        key={p.scene}
                        className="flex items-center gap-1 text-[10px] text-ash-dim"
                        title={p.names.join("  ·  ")}
                      >
                        <span className="font-mono text-ash">{p.scene || "—"}</span>
                        <span className="flex items-center gap-0.5">
                          {p.hasRef && (
                            <span className="rounded border border-rule px-1 text-[9px] text-ash">图</span>
                          )}
                          {p.hasVideo && (
                            <span className="rounded border border-wash-line px-1 text-[9px] text-paper">片</span>
                          )}
                        </span>
                      </span>
                    ))}
                  </span>
                )}
              </button>
            ))}
          </div>
        ))}
      </div>
    </aside>
  )
}

/** 把一段的关联文件按「场景」归组，配对参考图↔视频，返回 [{scene, hasRef, hasVideo, names}] */
function pairFiles(files: SegmentFile[]): { scene: string; hasRef: boolean; hasVideo: boolean; names: string[] }[] {
  const byScene = new Map<string, { hasRef: boolean; hasVideo: boolean; names: string[] }>()
  for (const f of files) {
    const key = f.scene || f.sub || "—"
    let g = byScene.get(key)
    if (!g) {
      g = { hasRef: false, hasVideo: false, names: [] }
      byScene.set(key, g)
    }
    if (f.kind === "ref") g.hasRef = true
    if (f.kind === "video") g.hasVideo = true
    g.names.push(f.name)
  }
  return [...byScene.entries()].map(([scene, g]) => ({ scene, ...g }))
}

/* 列宽用百分比写死，配合 table-fixed：
   自适应表格会把整张表撑到「最长那格」的宽度，画面内容一长就横向滚到看不全；
   固定表格则让长文字在格子里折行。三栏（画面 / 动作 / 台词）拿 53%，
   其余短列只留够读一行的宽度。 */
const COLUMNS: { key: keyof Shot; label: string; w: string; wide?: boolean }[] = [
  { key: "no", label: "镜号", w: "9%" },
  { key: "duration", label: "时长", w: "3%" },
  { key: "framing", label: "景别", w: "3.5%" },
  { key: "camera", label: "摄法", w: "3%" },
  { key: "position", label: "机位", w: "3.5%" },
  { key: "movement", label: "运镜", w: "3.5%" },
  { key: "content", label: "画面内容", w: "28%", wide: true },
  { key: "action", label: "动作", w: "13%", wide: true },
  { key: "dialogue_raw", label: "台词·声音", w: "10.5%", wide: true },
  { key: "sound", label: "音效", w: "5%" },
  { key: "light", label: "光线", w: "4.5%" },
  { key: "beat", label: "节拍", w: "3%" },
  { key: "risk", label: "风险", w: "5.5%" },
  { key: "material", label: "素材", w: "3%" },
]

function ShotTable({ shots, onOpen }: { shots: Shot[]; onOpen: (index: number) => void }) {
  if (!shots.length) {
    return <Empty title="这张主表里没有镜" body="可能只解析到表头。回源文件确认 21 字段表有没有内容行。" />
  }
  return (
    <table className="w-full min-w-[900px] table-fixed border-collapse text-[11.5px]">
      <colgroup>
        <col style={{ width: "2%" }} />
        {COLUMNS.map((c) => (
          <col key={c.key} style={{ width: c.w }} />
        ))}
      </colgroup>
      <thead className="sticky top-0 z-1 bg-panel">
        <tr className="border-b border-rule-soft text-left text-[10.5px] text-ash-dim">
          <th className="px-2 py-1.5 font-normal">#</th>
          {COLUMNS.map((c) => (
            <th key={c.key} className="px-2 py-1.5 font-normal whitespace-nowrap">
              {c.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {shots.map((shot, i) => (
          <tr
            key={`${shot.no}-${i}`}
            tabIndex={0}
            title="点开看这一镜的全部字段"
            onClick={() => onOpen(i)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault()
                onOpen(i)
              }
            }}
            className="cursor-pointer border-b border-rule-soft/60 align-top transition-colors hover:bg-cell focus:bg-cell focus:outline-none"
          >
            <td className="px-2 py-1.5 font-mono text-[10.5px] text-ash-dim">{i + 1}</td>
            {COLUMNS.map((c) => (
              <td
                key={c.key}
                className={cn(
                  // 长文字一律在格子里折行：宁可这一行变高，也不要横向滚到看不见后半句。
                  // 不写 whitespace-normal —— 默认就是 normal，写出来反而和下面的
                  // whitespace-nowrap 抢同一条属性，谁生效取决于样式表里两个类的先后。
                  "break-words px-2 py-1.5 text-ash",
                  c.key === "no" && "break-all font-mono text-[11px] text-paper",
                  c.key === "duration" && "whitespace-nowrap font-mono",
                  c.wide && "leading-relaxed text-paper"
                )}
              >
                {c.key === "duration" ? shot.duration.toFixed(1) : String(shot[c.key] ?? "")}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/* 单镜抽屉：表格只给「横向对比」，读全文交给它。
   21 个字段按「先看什么」排序：画面内容 → 动作 → 台词 → 其余技术字段。 */
const SHEET_FIELDS: { label: string; key: keyof Shot }[] = [
  { label: "画面内容", key: "content" },
  { label: "人物动作", key: "action" },
  { label: "台词·声音", key: "dialogue_raw" },
  { label: "音效·BGM", key: "sound" },
  { label: "转场", key: "transition" },
  { label: "光线", key: "light" },
  { label: "色彩·氛围", key: "color" },
  { label: "道具", key: "props" },
  { label: "服装·妆造", key: "costume" },
  { label: "特效·后期", key: "fx" },
  { label: "场次", key: "scene" },
  { label: "节拍·功能", key: "beat" },
  { label: "风险·备注", key: "risk" },
  { label: "素材编号", key: "material" },
]

/* 技术字段单独拿出来：它们是「查证用」的，不是「看画面用」的。
   卡片做小、只在有值时出现，空字段收成末尾一行「未填」，不占版面。
   场次/光线/色彩/服装·妆造 不在这里——它们已经升成「场景状态」「人物状态」两张主卡，
   留在这儿会跟主卡说同一件事，白占高度。 */
const MINI_FIELDS: { label: string; key: keyof Shot }[] = [
  { label: "音效·BGM", key: "sound" },
  { label: "转场", key: "transition" },
  { label: "道具", key: "props" },
  { label: "特效·后期", key: "fx" },
  { label: "节拍·功能", key: "beat" },
  { label: "风险·备注", key: "risk" },
  { label: "素材编号", key: "material" },
]

/* 分镜表里的空单元格常写成「—」「/」「无」，这些都不是内容。
   不认出来，弹窗就会铺一排只有一个破折号的卡片，把真正有字的挤下去。 */
const BLANK_MARKS = new Set(["", "—", "——", "-", "--", "/", "／", "无", "N/A", "n/a", "NA"])

function val(v: unknown): string {
  const s = String(v ?? "").trim()
  return BLANK_MARKS.has(s) ? "" : s
}

function shotText(shot: Shot, index: number): string {
  const head = `第 ${index + 1} 镜 · ${shot.no}（${shot.duration.toFixed(1)}s）`
  const body = SHEET_FIELDS.map((f) => `${f.label}：${String(shot[f.key] ?? "") || "—"}`)
  return [head, ...body].join("\n")
}

function ShotSheet({
  shot,
  index,
  total,
  onClose,
  onPrev,
  onNext,
}: {
  shot: Shot
  index: number
  total: number
  onClose: () => void
  onPrev: () => void
  onNext: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp") onPrev()
      else if (e.key === "ArrowRight" || e.key === "ArrowDown") onNext()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose, onPrev, onNext])

  const tech = [
    shot.framing && `景别 ${shot.framing}`,
    shot.camera && `摄法 ${shot.camera}`,
    shot.position && `机位 ${shot.position}`,
    shot.movement && `运镜 ${shot.movement}`,
    shot.focal && `焦段 ${shot.focal}`,
    `${shot.duration.toFixed(1)}s`,
    `${shot.start.toFixed(1)}–${shot.end.toFixed(1)}s`,
  ].filter(Boolean) as string[]

  const minis = MINI_FIELDS.map((f) => ({ ...f, value: val(shot[f.key]) }))
  const filled = minis.filter((m) => m.value)
  const blank = minis.filter((m) => !m.value).map((m) => m.label)
  const raw = val(shot.dialogue_raw)
  const pct = total > 0 ? ((index + 1) / total) * 100 : 0

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/65 p-4 sm:p-6"
      onClick={onClose}
      role="presentation"
    >
      {/* 高度用百分比封顶（max-h-[92%]，相对遮罩的可视区）：
          镜头文字再长也只在正文自己的滚动区里走，弹窗本身永远不越出屏幕，
          镜号和上一镜/下一镜始终钉在视口内，不用滚回去找。 */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`第 ${index + 1} 镜 ${shot.no}`}
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[92%] w-full max-w-[980px] flex-col overflow-hidden rounded-panel border border-rule bg-panel shadow-[0_24px_80px_rgba(0,0,0,.6)]"
      >
        <header className="shrink-0 border-b border-rule-soft px-4 pb-2.5 pt-3 sm:px-5">
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
                <span className="font-mono text-[16px] font-medium tracking-tight text-paper">
                  {shot.no}
                </span>
                <span className="text-[12px] text-ash">
                  第 {index + 1} / {total} 镜
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5 pt-2">
                {tech.map((t) => (
                  <span
                    key={t}
                    className="rounded border border-rule bg-cell px-2 py-1 text-[11.5px] text-paper"
                  >
                    {t}
                  </span>
                ))}
              </div>
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭"
              className="inline-flex size-7 flex-none items-center justify-center rounded-ctl border border-rule bg-cell text-ash transition-colors hover:bg-hi hover:text-paper"
            >
              <X className="size-3.5" />
            </button>
          </div>
          {/* 这一镜在整段里的位置：一条按比例走的线，翻镜时一眼知道还剩多少 */}
          <div className="mt-2.5 h-px w-full overflow-hidden bg-rule-soft">
            <div className="h-full bg-paper/70" style={{ width: `${pct}%` }} />
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-3 sm:px-5">
          {/* 主读区四张卡，2×2：左列「画面 / 场景」，右列「动作 / 人物」——
              先看拍什么、再看人怎么动，然后才是这一镜的环境状态和人本身的状态。
              两条并排才读得出分镜的因果，堆成上下两段就散了。 */}
          <div className="grid gap-2.5 lg:grid-cols-[1.35fr_1fr]">
            <BigCard label="画面内容" value={shot.content} primary />
            <BigCard label="人物动作" value={shot.action} />
            <SceneStateCard scene={shot.scene} light={shot.light} color={shot.color} />
            <BigCard label="人物状态" value={shot.costume} />
          </div>

          {(shot.dialogue.length > 0 || raw) && (
            <section className="mt-2.5 rounded-panel border border-rule-soft bg-cell px-3.5 py-2.5">
              <div className="flex items-center gap-1.5 pb-1.5 text-[11px] text-ash-dim">
                <span className="h-2.5 w-[2px] flex-none rounded-full bg-paper/70" />
                台词 · 声音
                <span className="flex-1" />
                {shot.dialogue.length > 0 && <span className="font-mono">{shot.dialogue.length} 条</span>}
              </div>
              {shot.dialogue.length > 0 ? (
                <ul className="flex flex-col gap-1.5">
                  {shot.dialogue.map((d, i) => (
                    <li key={i} className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                      <span className="text-[11.5px] text-ash">{d.who || "（未标）"}</span>
                      {d.kind === "独白" && (
                        <span className="rounded border border-rule px-1 text-[10px] leading-[16px] text-ash-dim">
                          内心独白
                        </span>
                      )}
                      <span className="min-w-0 break-words text-[13px] leading-[1.85] text-paper">
                        {d.line}
                      </span>
                      {val(d.lip) && <span className="text-[10.5px] text-ash-dim">{d.lip}</span>}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="whitespace-pre-wrap break-words text-[13px] leading-[1.85] text-paper">
                  {raw}
                </p>
              )}
            </section>
          )}

          {/* 技术字段：卡片做小，只铺有内容的；空的收成末尾一行，不占版面。 */}
          {filled.length > 0 && (
            <div className="mt-2.5 grid grid-cols-2 gap-2 lg:grid-cols-3">
              {filled.map((m) => (
                <MiniCard key={m.key} label={m.label} value={m.value} />
              ))}
            </div>
          )}
          {blank.length > 0 && (
            <p className="pt-2.5 text-[11px] leading-relaxed text-ash-dim">
              <span className="pr-1.5">未填</span>
              {blank.join(" / ")}
            </p>
          )}
        </div>

        <footer className="flex shrink-0 items-center gap-2 border-t border-rule-soft px-4 py-2.5 sm:px-5">
          <button
            type="button"
            onClick={onPrev}
            disabled={index === 0}
            className="inline-flex h-8 items-center gap-1 rounded-ctl border border-rule bg-cell px-2.5 text-[12px] text-ash transition-colors hover:bg-hi hover:text-paper disabled:opacity-40"
          >
            <ChevronLeft className="size-3.5" />
            上一镜
          </button>
          <button
            type="button"
            onClick={onNext}
            disabled={index >= total - 1}
            className="inline-flex h-8 items-center gap-1 rounded-ctl border border-rule bg-cell px-2.5 text-[12px] text-ash transition-colors hover:bg-hi hover:text-paper disabled:opacity-40"
          >
            下一镜
            <ChevronRight className="size-3.5" />
          </button>
          <span className="flex-1" />
          <span className="hidden text-[11px] text-ash-dim sm:inline">← → 切镜 · Esc 关闭</span>
          <button
            type="button"
            onClick={() => {
              void navigator.clipboard
                .writeText(shotText(shot, index))
                .then(() => toast.success("这一镜已复制"))
                .catch(() => toast.error("剪贴板不可用"))
            }}
            className="inline-flex h-8 items-center gap-1 rounded-ctl border border-rule bg-cell px-2.5 text-[12px] text-ash transition-colors hover:bg-hi hover:text-paper"
          >
            <Copy className="size-3" />
            复制这一镜
          </button>
        </footer>
      </div>
    </div>
  )
}

/* 主读卡：一张卡只说一件事。画面卡给足字号和行距（这是要「在脑子里成像」的那段），
   权重靠形状而不是颜色区分——画面用实心方块记号 + 亮边框，动作用竖条 + 暗边框。 */
function BigCard({ label, value, primary }: { label: string; value?: string; primary?: boolean }) {
  const text = val(value)
  return (
    <section
      className={cn(
        "flex min-w-0 flex-col rounded-panel border bg-cell px-3.5 py-3",
        primary ? "border-rule" : "border-rule-soft"
      )}
    >
      <div className="flex items-center gap-1.5 pb-2 text-[11px] text-ash-dim">
        {primary ? (
          <span className="size-[5px] flex-none rounded-[1px] bg-paper" />
        ) : (
          <span className="h-2.5 w-[2px] flex-none rounded-full bg-ash-dim" />
        )}
        {label}
      </div>
      <p
        className={cn(
          "min-w-0 whitespace-pre-wrap break-words text-paper",
          primary ? "text-[14.5px] leading-[1.9]" : "text-[13.5px] leading-[1.85]"
        )}
      >
        {text || <span className="text-ash-dim">这一镜没写</span>}
      </p>
    </section>
  )
}

/* 场景状态卡：分镜表里这件事被拆在「场次 / 光线 / 色彩·氛围」三列，
   拆着看读不出「这一镜的环境到底什么状态」，所以在同一张卡里按行拼回来。
   场次只当抬头（它是个编号，不该占正文字号），光线和色彩才是要读的内容。 */
function SceneStateCard({
  scene,
  light,
  color,
}: {
  scene?: string
  light?: string
  color?: string
}) {
  const head = val(scene)
  const rows = [
    { label: "光线", value: val(light) },
    { label: "色彩", value: val(color) },
  ].filter((r) => r.value)

  return (
    <section className="flex min-w-0 flex-col rounded-panel border border-rule-soft bg-cell px-3.5 py-3">
      <div className="flex items-center gap-1.5 pb-2 text-[11px] text-ash-dim">
        <span className="h-2.5 w-[2px] flex-none rounded-full bg-ash-dim" />
        场景状态
        {head && (
          <>
            <span className="flex-1" />
            <span className="truncate font-mono text-[10.5px] text-ash" title={head}>
              {head}
            </span>
          </>
        )}
      </div>
      {rows.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          {rows.map((r) => (
            <div key={r.label} className="flex gap-2">
              <span className="w-[26px] flex-none pt-[3px] text-[10.5px] text-ash-dim">{r.label}</span>
              <p className="min-w-0 whitespace-pre-wrap break-words text-[13px] leading-[1.8] text-paper">
                {r.value}
              </p>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-[13.5px] text-ash-dim">这一镜没写光线与色彩</p>
      )}
    </section>
  )
}

/* 技术字段卡：只负责「查得到」，字号压到比正文小一档，不让它们跟画面抢视线。 */
function MiniCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-ctl border border-rule-soft bg-cell px-2.5 py-2">
      <div className="text-[10.5px] text-ash-dim">{label}</div>
      <p className="whitespace-pre-wrap break-words pt-0.5 text-[12px] leading-[1.7] text-paper">
        {value}
      </p>
    </div>
  )
}

const KIND_LABEL: Record<MaterialRow["kind"], string> = {
  person: "人物",
  person_backup: "人脸备用",
  scene: "场景",
  prop: "道具",
  audio: "音色",
}

function MaterialTable({
  rows,
  scene,
  busy,
  onPick,
  onIgnore,
  onReset,
}: {
  rows: MaterialRow[]
  scene: string
  busy: boolean
  onPick: (row: MaterialRow) => void
  onIgnore: (row: MaterialRow) => void
  onReset: (row: MaterialRow) => void
}) {
  if (!rows.length) {
    return <Empty title="没有素材" body="分镜表的「服装·妆造」「道具」列是空的，或素材目录里没有可比对的图。" />
  }
  const unbound = rows.filter((r) => r.source === "missing").length
  return (
    <div className="p-4">
      <p className="mb-2.5 text-[11.5px] leading-relaxed text-ash">
        自动按「名字出现在文件名里 + 用途词加权」绑，场景取{" "}
        <span className="text-paper">{scene || "—"}</span>。绑错或绑不上就点「指定」换一张，
        不用改文件名；「忽略」的占位编号保留、不回收，也不会写进素材表。
        {unbound > 0 && (
          <span className="pl-1 text-paper">现在还有 {unbound} 项待指定。</span>
        )}
      </p>
      <table className="w-full border-collapse text-[11.5px]">
        <thead>
          <tr className="border-b border-rule-soft text-left text-[10.5px] text-ash-dim">
            <th className="py-1.5 pr-2 font-normal">占位</th>
            <th className="py-1.5 pr-2 font-normal">类型</th>
            <th className="py-1.5 pr-2 font-normal">名字</th>
            <th className="py-1.5 pr-2 font-normal">文件</th>
            <th className="py-1.5 pr-2 font-normal">来源</th>
            <th className="py-1.5 font-normal">操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.placeholder} className="border-b border-rule-soft/60 align-top">
              <td className="whitespace-nowrap py-1.5 pr-2 font-mono text-paper">{r.placeholder}</td>
              <td className="whitespace-nowrap py-1.5 pr-2 text-ash-dim">{KIND_LABEL[r.kind]}</td>
              <td className="whitespace-nowrap py-1.5 pr-2 text-ash">{r.name}</td>
              <td className="py-1.5 pr-2 font-mono text-[11px]">
                {r.file ? (
                  <span className={cn("break-all", r.source === "ignored" ? "text-ash-dim line-through" : "text-paper")}>
                    {r.file}
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-ash-dim">
                    <TriangleAlert className="size-3" />
                    待指定
                  </span>
                )}
              </td>
              <td className="whitespace-nowrap py-1.5 pr-2">
                <span
                  className={cn(
                    "rounded border px-1 text-[9.5px] leading-[15px]",
                    r.source === "manual"
                      ? "border-wash-line text-paper"
                      : r.source === "ignored"
                        ? "border-rule text-ash-dim"
                        : r.source === "missing"
                          ? "border-wash-line text-paper"
                          : "border-rule text-ash"
                  )}
                >
                  {SOURCE_TEXT[r.source]}
                </span>
              </td>
              <td className="whitespace-nowrap py-1.5">
                <span className="inline-flex items-center gap-1">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onPick(r)}
                    className="h-6 rounded-ctl border border-rule bg-cell px-1.5 text-[10.5px] text-ash transition-colors hover:bg-hi hover:text-paper disabled:opacity-40"
                  >
                    指定
                  </button>
                  {r.source !== "ignored" && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => onIgnore(r)}
                      className="h-6 rounded-ctl border border-rule bg-cell px-1.5 text-[10.5px] text-ash transition-colors hover:bg-hi hover:text-paper disabled:opacity-40"
                    >
                      忽略
                    </button>
                  )}
                  {r.source !== "auto" && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => onReset(r)}
                      className="h-6 rounded-ctl border border-rule bg-cell px-1.5 text-[10.5px] text-ash transition-colors hover:bg-hi hover:text-paper disabled:opacity-40"
                    >
                      恢复自动
                    </button>
                  )}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function FilePicker({
  kind,
  label,
  onClose,
  onPick,
}: {
  kind: "image" | "audio"
  label: string
  onClose: () => void
  onPick: (path: string) => void
}) {
  const [q, setQ] = useState("")
  const filesQ = useQuery({
    queryKey: [...PROMPTS_KEY_ROOT, "files", kind, q],
    queryFn: () => listFiles(kind, q),
    retry: 1,
  })
  const files = filesQ.data ?? []

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-auto bg-black/60 p-4 sm:p-8"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`为 ${label} 指定文件`}
        onClick={(e) => e.stopPropagation()}
        className="my-auto flex max-h-[82vh] w-full max-w-[860px] flex-col overflow-hidden rounded-panel border border-rule bg-panel shadow-[0_24px_80px_rgba(0,0,0,.6)]"
      >
        <header className="flex items-center gap-3 border-b border-rule-soft px-5 py-3">
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-medium text-paper">为 {label} 指定文件</div>
            <div className="pt-0.5 text-[10.5px] text-ash-dim">
              只列工作区素材目录里的{kind === "audio" ? "音频" : "图片"}，选中即绑定
            </div>
          </div>
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜文件名…"
            className="h-8 w-[220px] rounded-ctl border border-rule bg-cell px-2.5 text-[12px] outline-none placeholder:text-ash-dim focus:border-wash-line"
          />
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="inline-flex size-7 flex-none items-center justify-center rounded-ctl border border-rule bg-cell text-ash transition-colors hover:bg-hi hover:text-paper"
          >
            <X className="size-3.5" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-auto p-4">
          {filesQ.isPending && <p className="text-[12px] text-ash">读文件列表…</p>}
          {!filesQ.isPending && !files.length && (
            <p className="text-[12px] text-ash">没有匹配的文件。清掉搜索词再看看。</p>
          )}
          {kind === "image" ? (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-2">
              {files.map((f) => (
                <button
                  key={f.path}
                  type="button"
                  onClick={() => onPick(f.path)}
                  title={f.path}
                  className="group flex flex-col overflow-hidden rounded-ctl border border-rule bg-cell text-left transition-colors hover:border-wash-line hover:bg-hi"
                >
                  <img
                    src={`/api/thumb?path=${encodeURIComponent(f.path)}`}
                    alt={f.name}
                    loading="lazy"
                    className="h-[104px] w-full bg-void object-cover"
                  />
                  <span className="truncate px-2 py-1.5 text-[10.5px] text-ash group-hover:text-paper">
                    {f.name}
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <ul className="flex flex-col gap-1">
              {files.map((f) => (
                <li key={f.path}>
                  <button
                    type="button"
                    onClick={() => onPick(f.path)}
                    className="flex w-full items-center gap-2 rounded-ctl border border-transparent px-2.5 py-1.5 text-left transition-colors hover:border-rule hover:bg-cell"
                  >
                    <span className="flex-1 truncate font-mono text-[11.5px] text-paper">{f.name}</span>
                    <span className="truncate font-mono text-[10px] text-ash-dim">{f.dir}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}

function IssueList({ issues }: { issues: { kind: string; field: string; detail: string }[] }) {
  if (!issues.length) {
    return (
      <div className="flex items-center gap-2 p-6 text-[12px] text-ash">
        <Check className="size-3.5" />
        21 字段齐全、素材全部绑定、时长与抬头对得上。
      </div>
    )
  }
  return (
    <ul className="flex flex-col gap-2 p-4">
      {issues.map((it, i) => (
        <li key={`${it.kind}-${i}`} className="rounded-ctl border border-rule bg-cell px-3 py-2">
          <div className="flex items-center gap-2 text-[11px] text-ash-dim">
            <span className="font-mono">{it.kind}</span>
            {it.field && <span>· {it.field}</span>}
          </div>
          <p className="pt-0.5 text-[12px] text-paper">{it.detail}</p>
        </li>
      ))}
    </ul>
  )
}

function BlockView({ text, hint, onCopy }: { text: string; hint: string; onCopy: () => void }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-rule-soft/60 px-3.5 py-1.5">
        <span className="flex-1 truncate text-[10.5px] text-ash-dim">{hint}</span>
        <button
          type="button"
          onClick={onCopy}
          className="inline-flex h-6 items-center gap-1 rounded-ctl border border-rule bg-cell px-1.5 text-[10.5px] text-ash transition-colors hover:bg-hi hover:text-paper"
        >
          <Copy className="size-3" />
          复制
        </button>
      </div>
      <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap px-3.5 py-3 font-mono text-[11.5px] leading-[1.8] text-paper">
        {text || "这一段还没渲染出来。"}
      </pre>
    </div>
  )
}

function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="grid place-items-center p-10">
      <div className="max-w-[46ch] text-center">
        <p className="text-[13px] text-paper">{title}</p>
        <p className="mt-1.5 text-[12px] leading-relaxed text-ash">{body}</p>
      </div>
    </div>
  )
}
