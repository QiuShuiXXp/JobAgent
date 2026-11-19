"""桌面运行目录的显式初始化：建 %LOCALAPPDATA%\\JobAgent，迁配置，密钥进凭据管理器。

首启其实不必跑它 —— settings 装载前会自愈（bootstrap.ensure_desktop_ready）。
这个脚本留给两件事：把仓库 .env 里的 LLM key 迁进凭据管理器，以及 --reset 重来。
    uv run python scripts/init_desktop.py            # 建/更新运行目录
    uv run python scripts/init_desktop.py --no-llm   # 不把仓库 .env 里的 LLM key 迁过去
    uv run python scripts/init_desktop.py --reset    # 删掉桌面运行目录重来（危险，先确认）

--reset 只删 %LOCALAPPDATA%\\JobAgent，不动仓库里的 data/ 与 .env。
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8", errors="replace")  # GBK 控制台会打乱中文


def _mask(dsn: str) -> str:
    """把 DSN 里的密码换成 *** ，其余照打 —— 这行会留在终端历史里。"""
    return re.sub(r"://([^/:@]+):[^@/]+@", r"://\1:***@", dsn)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-llm", action="store_true", help="不迁移 .env 里的 LLM key")
    parser.add_argument("--reset", action="store_true", help="删除桌面运行目录后重建")
    args = parser.parse_args()

    from jobagent.config import bootstrap, paths

    if args.reset:
        home = paths.desktop_home()
        if home == paths.ASSETS_ROOT:
            print("[reset] ✗ 桌面运行目录指向仓库根，拒绝删除；"
                  "要重置请显式设 JOBAGENT_HOME 到一个独立目录")
            return
        if not paths.desktop_config_file().exists():
            print(f"[reset] {home} 没有 config.env，看起来不是本程序的运行目录，跳过")
            return
        shutil.rmtree(home)
        print(f"[reset] ✓ 已删除 {home}")

    report = bootstrap.init_desktop(migrate_llm_key=not args.no_llm)
    for key, value in report.items():
        print(f"[init] {key}: {value}")

    # 配置文件写好后才算桌面形态，这里重新装载一次，证明分家后的链条真能读通
    from jobagent.config.settings import Settings
    fresh = Settings()
    print(f"[check] mode={'desktop' if paths.is_desktop() else 'dev'}")
    print(f"[check] db={_mask(fresh.data_base_url)}")
    print(f"[check] storage={fresh.storage_root}")
    print(f"[check] listen={fresh.app_host}:{fresh.app_port}")
    print(f"[check] llm key 来自{'凭据管理器/配置文件' if fresh.llm_api_key else '（缺失）'}，"
          f"长度 {len(fresh.llm_api_key)}（值不打印）")


if __name__ == "__main__":
    main()
