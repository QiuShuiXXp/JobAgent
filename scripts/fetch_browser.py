"""拉一份 Chrome for Testing 到 desktop/vendor/chrome-win64，给 dev 形态当本机浏览器。

为什么要留这一份：dev 形态（在普通浏览器里开 localhost）是后端自己拉浏览器，而
DrissionPage 只认 chrome.exe（不找 Edge），本机没装 Chrome 就起不来。
桌面分发包已经不带浏览器了 —— BOSS 页面跑在壳内嵌的 WebView2 里，见 scripts/make_dist.py。

用法：
    python scripts/fetch_browser.py            # 拉当前 Stable，已存在且版本相同则跳过
    python scripts/fetch_browser.py --force    # 重新下载解压
产物：
    desktop/vendor/chrome-win64/            浏览器本体（detect_browser_path() 认这个位置）
    desktop/vendor/BROWSER_VERSION          版本号，供出包与回滚对账
"""
import argparse
import json
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VENDOR = REPO / "desktop" / "vendor"
BROWSER_DIR = VENDOR / "chrome-win64"
VERSION_FILE = VENDOR / "BROWSER_VERSION"
API = ("https://googlechromelabs.github.io/"
       "chrome-for-testing/last-known-good-versions-with-downloads.json")
PLATFORM = "win64"


def download(url: str, dest: Path) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "JobAgent/fetch_browser"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done / 1e6:6.0f} / {total / 1e6:.0f} MB", end="", flush=True)
    print()
    return dest


def resolve_url(version: str | None) -> tuple[str, str]:
    """返回 (版本号, zip 地址)。不指定版本就取 Stable 通道最新。"""
    if version:
        return version, (f"https://storage.googleapis.com/chrome-for-testing-public/"
                         f"{version}/{PLATFORM}/chrome-{PLATFORM}.zip")
    with urllib.request.urlopen(API, timeout=60) as resp:
        data = json.load(resp)
    channel = data["channels"]["Stable"]
    # downloads 是按产物分组的字典：chrome / chromedriver / testing_profile
    entry = next(d for d in channel["downloads"]["chrome"] if d["platform"] == PLATFORM)
    return channel["version"], entry["url"]


def install(zip_path: Path) -> None:
    """解压并换掉旧目录：先移到 .old 再删，中途失败不至于留下半个浏览器。"""
    staging = Path(tempfile.mkdtemp(prefix="cft-", dir=str(VENDOR)))
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(staging)
    inner = staging / f"chrome-{PLATFORM}"
    if not inner.is_dir():                    # 目录名偶数不带平台后缀时兜底
        inner = next(p for p in staging.iterdir() if p.is_dir())

    backup = VENDOR / "chrome-win64.old"
    if BROWSER_DIR.exists():
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        BROWSER_DIR.rename(backup)
    try:
        inner.rename(BROWSER_DIR)
    except OSError:                           # 跨卷 rename 失败（vendor 在别的盘时）
        shutil.copytree(inner, BROWSER_DIR)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", help="指定 Chrome for Testing 版本号")
    ap.add_argument("--force", action="store_true", help="忽略已装版本，重新下载")
    args = ap.parse_args()

    version, url = resolve_url(args.version)
    current = VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.is_file() else ""
    chrome_exe = BROWSER_DIR / "chrome.exe"
    if not args.force and current == version and chrome_exe.is_file():
        print(f"已内置 {version}，无需更新")
        return 0

    VENDOR.mkdir(parents=True, exist_ok=True)
    print(f"下载 Chrome for Testing {version} ({PLATFORM})")
    # zip 落在 vendor 里而不是 %TEMP%：C 盘常年吃紧，190MB 没必要往系统盘塞
    tmp = VENDOR / "chrome-download.tmp.zip"
    try:
        download(url, tmp)
        install(tmp)
    finally:
        tmp.unlink(missing_ok=True)

    if not chrome_exe.is_file():
        print(f"解压后没找到 {chrome_exe}", file=sys.stderr)
        return 1
    VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    size = sum(f.stat().st_size for f in BROWSER_DIR.rglob("*") if f.is_file()) / 1e6
    print(f"内置浏览器就绪：{chrome_exe}\n  版本 {version}，解压后 {size:.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
