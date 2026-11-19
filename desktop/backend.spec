# -*- mode: python ; coding: utf-8 -*-
"""把后端冻成免安装的 jobagent-backend/ 目录。

    uv run pyinstaller desktop/backend.spec --noconfirm

用 onedir 不用 onefile：onefile 每次启动都要把整个解包到临时目录，
langchain + DrissionPage 这个体量冷启动能慢到让用户以为没双击上。
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent      # spec 在 desktop/ 下，仓库根是它的上一级

# 只读资源：页面、流程定义、提示词模板（loader 用 __file__ 的包内相对路径找它们）
datas = [
    (str(ROOT / "web"), "web"),
    (str(ROOT / "flow_config"), "flow_config"),
    (str(ROOT / "jobagent" / "prompts" / "jinja2"), "jobagent/prompts/jinja2"),
]
datas += collect_data_files("DrissionPage")
datas += collect_data_files("keyring")
# keyring 靠 entry_points 找后端，元数据没打进来就报 "No recommended backend"
datas += copy_metadata("keyring")
datas += copy_metadata("langchain-core")
datas += copy_metadata("pydantic")

hiddenimports = [
    # uvicorn 按字符串动态挑实现，静态分析看不见
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "aiosqlite",
    "greenlet",
]
for _pkg in ("DrissionPage", "langchain_core", "langchain_openai", "jobagent"):
    hiddenimports += collect_submodules(_pkg)

a = Analysis(
    [str(ROOT / "desktop" / "backend_main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide6", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="jobagent-backend",
    debug=False,
    strip=False,
    upx=False,
    # 控制台子系统：出问题时能直接在终端里跑这个 exe 看报错。
    # 桌面壳拉起时用 CREATE_NO_WINDOW 把黑框挡掉，用户看不到它。
    console=True,
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="jobagent-backend")
