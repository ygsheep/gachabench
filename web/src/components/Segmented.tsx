import { cn } from "cn"

/** 状态分段控件（全部 / 草稿 / 正片 / 废稿）。滑块跟着选项均分，不按文字量算。 */
export function Segmented({
  value,
  onChange,
  options,
}: {
  value: string
  onChange: (v: string) => void
  options: { v: string; label: string; n: number }[]
}) {
  const i = Math.max(
    0,
    options.findIndex((o) => o.v === value)
  )
  const W = 100 / options.length
  return (
    <div className="relative inline-flex flex-none rounded-ctl border border-rule bg-panel p-0.5">
      <span
        aria-hidden
        className="pointer-events-none absolute bottom-0.5 top-0.5 rounded-[4px] border border-[#565656] bg-hi transition-transform duration-300 ease-[cubic-bezier(.2,.8,.25,1)]"
        style={{
          width: `calc(${W}% - 4px)`,
          transform: `translateX(calc(${i * 100}% + ${i * 4}px))`,
        }}
      />
      {options.map((o) => (
        <button
          key={o.v}
          type="button"
          onClick={() => onChange(o.v)}
          aria-pressed={o.v === value}
          className={cn(
            "relative z-1 inline-flex h-[26px] items-center gap-1.5 rounded-[4px] px-2.5 text-[12.5px] transition-colors",
            o.v === value ? "font-medium text-paper" : "text-ash hover:text-paper"
          )}
        >
          {o.label}
          <span
            className={cn("font-mono text-[11px]", o.v === value ? "text-white" : "text-ash-dim")}
          >
            {o.n}
          </span>
        </button>
      ))}
    </div>
  )
}
