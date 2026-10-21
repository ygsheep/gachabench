# -*- coding: utf-8 -*-
"""从 Google Fonts css2 响应里抽取 latin 子集并下载，生成本地 @font-face CSS。

可变字体去重：Google Fonts 现在对 IBM Plex Sans 这类家族返回**同一个** woff2
（内部是可变字体，一个文件覆盖整个 weight 轴）。此时注册成一个 face 并用
`font-weight: <min> <max>` 范围声明；静态分重量的家族（如 Plex Mono）仍是
一个 weight 一个 face。
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "public" / "fonts"


def download(url: str, dest: pathlib.Path) -> None:
    r = subprocess.run(["curl", "-sL", "--max-time", "60", url, "-o", str(dest)], capture_output=True)
    size = dest.stat().st_size if dest.exists() else 0
    print(f"{dest.name}: {size} bytes (curl exit {r.returncode})")
    if size < 1000:
        sys.exit(f"下载可疑：{dest.name}")


def face(lines: list, fam: str, weight: str, fname: str) -> None:
    lines += [
        "@font-face {",
        f"  font-family: '{fam}';",
        "  font-style: normal;",
        f"  font-weight: {weight};",
        "  font-display: swap;",
        f"  src: url('/fonts/{fname}') format('woff2');",
        "}",
    ]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    css = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")

    # /* latin */ 注释后面跟着一个 @font-face 块
    blocks = re.findall(r"/\*\s*([\w-]+)\s*\*/\s*@font-face\s*\{(.*?)\}", css, re.S)
    families: dict[str, dict[str, str]] = {}
    for subset, body in blocks:
        if subset != "latin":
            continue
        fam = re.search(r"font-family:\s*'([^']+)'", body).group(1)
        weight = re.search(r"font-weight:\s*(\d+)", body).group(1)
        url = re.search(r"url\((https://[^)]+)\)", body).group(1)
        families.setdefault(fam, {})[weight] = url

    lines = [
        "/* IBM Plex 本地化字体（latin 子集）——由 tools/fetch_fonts.py 生成，勿手改。",
        "   中文不走 Plex：index.css 的 font-family fallback 会落到 PingFang/雅黑。 */",
    ]
    for fam, weights in sorted(families.items()):
        slug = fam.lower().replace(" ", "-")
        urls = set(weights.values())
        if len(urls) == 1 and len(weights) > 1:
            # 可变字体：一个文件覆盖整个 weight 轴
            lo, hi = min(int(w) for w in weights), max(int(w) for w in weights)
            fname = f"{slug}-latin-var.woff2"
            download(next(iter(urls)), OUT / fname)
            face(lines, fam, f"{lo} {hi}", fname)
        else:
            for weight, url in sorted(weights.items(), key=lambda kv: int(kv[0])):
                fname = f"{slug}-latin-{weight}.woff2"
                download(url, OUT / fname)
                face(lines, fam, weight, fname)

    (OUT / "fonts.css").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nfonts.css written: {len(families)} families")


if __name__ == "__main__":
    main()
