/**
 * 接续轨 —— 全页唯一的高亮。
 * 与上面的胶片格同宽同距，把 3×5s 拼回 15s 这件事直接画出来：
 * 段齐了就是一道常亮浅轨，缺块处留虚线断口。
 */
export function StitchRail({
  segs,
  sum,
  full,
  focus,
}: {
  segs: { has: boolean }[]
  sum: string
  full: boolean
  focus: boolean
}) {
  return (
    <div className="gb-rail" data-full={full ? "1" : "0"} data-focus={focus ? "1" : "0"}>
      {segs.map((s, i) => (
        <div key={i} className="gb-seg" data-has={s.has ? "1" : "0"}>
          <i />
        </div>
      ))}
      <span className="gb-sum">{sum}</span>
    </div>
  )
}
