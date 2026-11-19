"""冻结后的后端入口：打包成 exe 就没有 `python -m uvicorn` 这条路了，自己调 uvicorn.run。

host/port 读 settings —— 桌面壳按固定端口连它，和 dev 那条命令行等价。
    uv run python desktop/backend_main.py
"""
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):   # 冻结后端也可能被人在终端里直接跑
    _stream.reconfigure(encoding="utf-8", errors="replace")

if not getattr(sys, "frozen", False):
    # 冻结时 jobagent 已经在包里，只有裸跑这个文件才需要把仓库根塞进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn

from jobagent.config.settings import settings

if __name__ == "__main__":
    uvicorn.run("jobagent.api.app:app", host=settings.app_host, port=settings.app_port)
