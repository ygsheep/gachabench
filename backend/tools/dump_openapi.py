# -*- coding: utf-8 -*-
r"""导出 FastAPI 的 OpenAPI 到指定文件。

用法（在 backend/ 下）：
    .\.venv\Scripts\python.exe tools\dump_openapi.py ..\web\src\lib\openapi.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 直接以脚本运行时，sys.path[0] 是 tools/，把 backend/ 补进来才能 import main
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import app  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "../web/src/lib/openapi.json").resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"openapi -> {out}")


if __name__ == "__main__":
    main()
