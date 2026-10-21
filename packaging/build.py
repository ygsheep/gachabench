# -*- coding: utf-8 -*-
"""GachaBench 一键打包：Tauri 桌面壳 + Python 后端 sidecar。

    uv run --project backend --with pyinstaller python packaging/build.py

流水线：
  ① npm run build                    → web/dist（React 静态产物）
  ② 暂存 ffmpeg                      → build/stage-ffmpeg/
  ③ PyInstaller onedir               → build/GachaBench-backend/（内含 _internal/ffmpeg）
  ④ 搬进 desktop/src-tauri/backend/  → Tauri bundle.resources 的入口
  ⑤ tauri build                      → desktop/src-tauri/target/release/

为什么用 `uv run --with pyinstaller`：后端 venv 是 uv 管理的、里面没有 pip，
而 PyInstaller 只是**构建期**工具，不该写进运行时依赖。
Tauri CLI 走 desktop/ 自己的 node_modules；Rust 工具链需另行安装
（`winget install Rustlang.Rustup`），MSVC 用 VS 的 C++ 工作负载。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PACKAGING = Path(__file__).resolve().parent
PROJECT = PACKAGING.parent
BACKEND = PROJECT / "backend"
WEB = PROJECT / "web"
DESKTOP = PROJECT / "desktop"
SRC_TAURI = DESKTOP / "src-tauri"
SPEC = PACKAGING / "gachabench.spec"

BUILD = PROJECT / "build"
STAGE_FFMPEG = BUILD / "stage-ffmpeg"
BACKEND_OUT = BUILD / "GachaBench-backend"      # PyInstaller onedir 产物
TAURI_BACKEND = SRC_TAURI / "backend"           # 给 bundle.resources 用


def _find_npm() -> str | None:
    """⚠️ 必须优先找 npm.cmd：Git Bash 的 PATH 里那个无扩展名的 `npm` 是 shell 脚本，
    CreateProcess 起不来它。"""
    names = ("npm.cmd", "npm.exe", "npm") if os.name == "nt" else ("npm",)
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def build_frontend(force: bool) -> None:
    index = WEB / "dist" / "index.html"
    if index.is_file() and not force:
        print(f"[1/5] 复用已有前端产物：{index.relative_to(PROJECT)}")
        return
    npm = _find_npm()
    if not npm:
        raise SystemExit(
            "[1/5] 找不到 npm，无法构建前端。\n"
            "      装好 Node 再跑，或先手动 `cd web && npm run build` 并加 --skip-frontend。"
        )
    print(f"[1/5] 构建前端：npm run build（{WEB}）")
    subprocess.run([npm, "run", "build"], cwd=str(WEB), check=True)


# ffplay 是播放器，本产品只用 ffmpeg / ffprobe，不打它（省 17MB）
FFMPEG_EXES = ("ffmpeg.exe", "ffprobe.exe")


def stage_ffmpeg() -> Path | None:
    """把 ffmpeg / ffprobe 连同依赖 DLL 暂存到 build/stage-ffmpeg/。

    来源优先级：GACHABENCH_FFMPEG_SRC 环境变量 → packaging/vendor/ffmpeg/ → PATH 里的 ffmpeg。
    """
    src: Path | None = None
    env_src = os.environ.get("GACHABENCH_FFMPEG_SRC")
    if env_src and Path(env_src).is_dir():
        src = Path(env_src)
    elif (PACKAGING / "vendor" / "ffmpeg").is_dir():
        src = PACKAGING / "vendor" / "ffmpeg"
    else:
        found = shutil.which("ffmpeg")
        if found:
            src = Path(found).resolve().parent

    if src is None:
        print("[2/5] 没找到 ffmpeg，本包将不带它（运行机需自备 PATH）")
        return None

    if STAGE_FFMPEG.exists():
        shutil.rmtree(STAGE_FFMPEG)
    STAGE_FFMPEG.mkdir(parents=True)

    copied: list[Path] = []
    for f in sorted(src.iterdir()):
        if not f.is_file():
            continue
        # 只要两个 exe + dll；ffplay 和其他杂物不要
        if f.name.lower() in FFMPEG_EXES or f.suffix.lower() == ".dll":
            shutil.copy2(f, STAGE_FFMPEG / f.name)
            copied.append(f)

    if not any(p.name.lower() == "ffmpeg.exe" for p in copied):
        print(f"[2/5] {src} 里没有 ffmpeg.exe，放弃内置")
        shutil.rmtree(STAGE_FFMPEG)
        return None

    size = sum(p.stat().st_size for p in copied) / 1024 / 1024
    print(f"[2/5] 内置 ffmpeg：{len(copied)} 个文件 / {size:.0f} MB ← {src}")
    return STAGE_FFMPEG


def check_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit(
            "[3/5] 当前环境没有 PyInstaller。请这样跑：\n"
            "      uv run --project backend --with pyinstaller python packaging/build.py\n"
            "      （不要 `uv add pyinstaller`：它只是构建期工具，不该进运行时依赖）"
        )


def run_pyinstaller(ffmpeg_dir: Path | None) -> None:
    env = dict(os.environ)
    if ffmpeg_dir:
        env["GB_FFMPEG_SRC"] = str(ffmpeg_dir)
    else:
        env.pop("GB_FFMPEG_SRC", None)

    BUILD.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--distpath", str(BUILD),
        "--workpath", str(BUILD / "pyinstaller"),
        str(SPEC),
    ]
    print("[3/5] PyInstaller 打包后端 sidecar…")
    subprocess.run(cmd, check=True, env=env)

    if not (BACKEND_OUT / "GachaBench-backend.exe").is_file():
        raise SystemExit(f"[3/5] 打包结束但没找到产物：{BACKEND_OUT}")
    ff = BACKEND_OUT / "_internal" / "ffmpeg" / "ffmpeg.exe"
    print(f"      → {BACKEND_OUT}"
          + ("（含内置 ffmpeg）" if ff.is_file() else "（不含 ffmpeg）"))


def stage_backend() -> None:
    """把 onedir 产物搬进 src-tauri/backend/，让 bundle.resources 能整目录打进去。"""
    if TAURI_BACKEND.exists():
        shutil.rmtree(TAURI_BACKEND)
    shutil.copytree(BACKEND_OUT, TAURI_BACKEND)
    n = sum(1 for p in TAURI_BACKEND.rglob("*") if p.is_file())
    print(f"[4/5] 后端搬进 Tauri 资源目录：{n} 个文件")


PROXY_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)


def run_tauri(target_dir: Path | None = None, installer: bool = False) -> Path:
    """调 tauri build。需要 cargo 在 PATH 里（~/.cargo/bin）和 MSVC 工具链。"""
    cargo_home = Path.home() / ".cargo" / "bin"
    env = dict(os.environ)
    env["PATH"] = str(cargo_home) + os.pathsep + env.get("PATH", "")

    # 🔴 摘掉代理：tauri build 要从 GitHub 下 NSIS，而 reqwest 认 HTTP(S)_PROXY。
    # 本机代理会把 CONNECT 挡成 502，于是整个打包步骤挂在最后一步。
    # （npm 已经跑完了才走到这里，所以摘掉不影响前端安装。）
    dropped = [v for v in PROXY_VARS if v in env]
    for v in PROXY_VARS:
        env.pop(v, None)
    if dropped:
        print(f"      已忽略代理变量：{' '.join(dropped)}")

    if target_dir:
        env["CARGO_TARGET_DIR"] = str(target_dir)

    cli = DESKTOP / "node_modules" / ".bin" / ("tauri.CMD" if os.name == "nt" else "tauri")
    if not cli.is_file():
        npm = _find_npm()
        if not npm:
            raise SystemExit("[5/5] desktop/ 下没有 @tauri-apps/cli，也找不到 npm")
        print("[5/5] 安装 @tauri-apps/cli …")
        subprocess.run([npm, "install"], cwd=str(DESKTOP), check=True)

    cmd = [str(cli), "build"]
    if not installer:
        # 默认不打安装包：NSIS/WiX 都要从 GitHub 下，而国内网络基本连不上。
        # 「可直接运行的文件夹」不需要安装包，见 assemble_runnable()。
        cmd.append("--no-bundle")
        print("[5/5] tauri build --no-bundle（不打安装包，GitHub 不可达）…")
    else:
        print("[5/5] tauri build（含 NSIS 安装包，需能访问 GitHub）…")

    t0 = time.time()
    subprocess.run(cmd, cwd=str(DESKTOP), check=True, env=env)
    print(f"      编译耗时 {time.time() - t0:.0f}s")

    base = target_dir or (SRC_TAURI / "target")
    binary = base / "release" / "gachabench.exe"
    if not binary.is_file():
        raise SystemExit(f"[5/5] tauri build 结束但没找到二进制：{binary}")
    return binary


def assemble_runnable(binary: Path, target_dir: Path) -> Path:
    """把 exe + 后端 sidecar 组装成一个可直接运行的文件夹。

    为什么自己组装而不用 Tauri 的 bundler：bundler 要从 GitHub 下 NSIS，
    国内网络基本连不上；而「可直接运行的文件夹」本来就不需要安装包。
    布局与 Tauri 正式 bundle 一致（`GachaBench.exe` + `resources/`），
    所以哪天网络通了改用 `--installer` 也能直接出安装包。

    ⚠️ **不要**往这里塞 `WebView2Loader.dll`：wry 在 Windows 上是静态链接
    WebView2 加载器的（`WebView2LoaderStatic.lib`），exe 的导入表里根本没有它。
    多拷一份纯属累赘 —— 实测还在排查上浪费了大量时间。
    """
    out = PROJECT / "dist" / "GachaBench"
    if out.exists():
        shutil.rmtree(out)
    (out / "resources").mkdir(parents=True)

    shutil.copy2(binary, out / "GachaBench.exe")

    # 后端整个 onedir（含 _internal/ffmpeg 与 _internal/web/dist）
    dest = out / "resources" / "backend"
    shutil.copytree(TAURI_BACKEND, dest)

    # 上一轮测试可能在这里留下 config.json，清掉，保证出货目录是干净的
    stale = dest / "config.json"
    if stale.is_file():
        stale.unlink()

    n = sum(1 for p in dest.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) / 1024 / 1024
    print(f"      组装完成：{out}")
    print(f"        GachaBench.exe + resources/backend（{n} 个文件）")
    print(f"        总体积 {size:.0f} MB")
    return out


def report(out: Path, binary: Path, target_dir: Path) -> None:
    nsis = target_dir / "release" / "bundle" / "nsis"
    print("\n完成")
    print(f"  可直接运行的文件夹：{out}")
    print(f"    主程序：{out / 'GachaBench.exe'}")
    print(f"    后端：  {out / 'resources' / 'backend' / 'GachaBench-backend.exe'}")
    if nsis.is_dir():
        installers = sorted(nsis.glob("*.exe"))
        if installers:
            print(f"  安装包：  {installers[-1]}  ({installers[-1].stat().st_size / 1024 / 1024:.0f} MB)")
    print("  配置：首次运行在 exe 同目录写出 config.json（backend/config.json 的副本）")
    print("  ffmpeg：已内置在后端 _internal/ffmpeg/，运行机无需自备")
    print("  日志：%LOCALAPPDATA%\\GachaBench\\logs\\（shell.log + backend.log，起不来时看这个）")


def main() -> None:
    ap = argparse.ArgumentParser(description="把 GachaBench 打成 Tauri 桌面应用")
    ap.add_argument("--frontend", action="store_true", help="强制重建前端")
    ap.add_argument("--skip-frontend", action="store_true", help="完全不碰前端")
    ap.add_argument("--skip-pyinstaller", action="store_true",
                    help="跳过 PyInstaller（复用 build/GachaBench-backend/，只重打包 Tauri）")
    ap.add_argument("--target-dir", type=Path, default=None,
                    help="Rust target 目录。默认 src-tauri/target；"
                         "某些沙箱不允许执行工作区内新生成的 exe，"
                         "Rust build script 的探针会因此失败，此时指到工作区外")
    ap.add_argument("--installer", action="store_true",
                    help="额外打 NSIS 安装包（需能访问 GitHub 下载 NSIS）")
    ap.add_argument("--skip-tauri", action="store_true", help="只打后端 sidecar，不跑 tauri build")
    args = ap.parse_args()

    if not args.skip_frontend:
        build_frontend(args.frontend)
    else:
        print("[1/5] 跳过前端（--skip-frontend）")
        if not (WEB / "dist" / "index.html").is_file():
            raise SystemExit("[1/5] web/dist 不存在，跳过前端会打出没有界面的应用。")

    if not args.skip_pyinstaller:
        ffmpeg_dir = stage_ffmpeg()
        check_pyinstaller()
        run_pyinstaller(ffmpeg_dir)
        stage_backend()
    else:
        print("[2/5~4/5] 跳过 PyInstaller，复用 build/GachaBench-backend/")
        if not (BACKEND_OUT / "GachaBench-backend.exe").is_file():
            raise SystemExit(f"[2/5] {BACKEND_OUT} 不存在，没法跳过")
        stage_backend()

    if args.skip_tauri:
        print(f"\n后端 sidecar 就绪：{BACKEND_OUT}")
        print("（--skip-tauri：没有跑 tauri build）")
        return

    target_dir = args.target_dir or (SRC_TAURI / "target")
    binary = run_tauri(target_dir, args.installer)
    out = assemble_runnable(binary, target_dir)
    report(out, binary, target_dir)


if __name__ == "__main__":
    main()
