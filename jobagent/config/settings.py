"""配置：非密钥项来自 config.env（桌面）或仓库 .env（dev），密钥按形态分流。

见 config/paths.py 的分家规则；这里只负责把相对路径钉成绝对路径，
并在桌面形态从凭据管理器取密钥——切换 cwd 再也不会让 storage/ 指错地方。

桌面形态在构造 Settings() 之前先跑一次 bootstrap.ensure_desktop_ready()，
所以全新机器第一次启动就能自己长出运行目录和密钥，不需要先手动初始化。
"""
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jobagent.config import bootstrap
from jobagent.config.paths import (
    DESKTOP_HOST, DESKTOP_PORT, app_home, config_file, db_file, is_desktop, storage_dir,
)
from jobagent.config.secrets import LLM_KEY_NAME, QIANFAN_KEY_NAME

# 只能在 Settings() 构造前跑：桌面首启要自己把 config.env 长出来，
# 而 env_file 的路径是类定义时就算好的，晚一步读到的就是空配置
bootstrap.ensure_desktop_ready()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(config_file()),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_base_url: str = ""      # 空则兜底到本地 SQLite（桌面进运行目录，dev 进仓库根）
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = ""
    llm_api_key: str = ""

    qianfan_mcp_url: str = "https://qianfan.baidubce.com/v2/ai_search/mcp"
    qianfan_api_key: str = ""

    # 工作记忆窗口（history/window.py）：轮数 + 字符预算，字符数是 token 的粗代理
    history_window_turns: int = 3
    history_window_chars: int = 4000
    # 情景记忆（滚动折叠）：每 fold_every 轮折一批，关闭时折尾；
    # 整场不足 min_turns 轮不值得摘要；summary 超长时硬截断
    session_summary_fold_every: int = 15
    session_summary_min_turns: int = 3
    session_summary_max_chars: int = 1200

    app_host: str = DESKTOP_HOST
    app_port: int = DESKTOP_PORT   # 桌面壳按固定端口连后端，desktop/ 的 Tauri 配置要同步这个值
    platform_base_url: str = "https://www.zhipin.com"
    # 浏览器 exe 路径：留空走自动探测（内置 → 系统 Chrome → 系统 Edge）。
    # 分发包里自带 chrome-win64 时一般用不上，留给便携版/国产套壳 Chromium 兜底。
    browser_path: str = ""
    # 只有 dev 形态用得上（桌面形态的页面是壳内嵌 WebView2，可见性由壳决定）：
    # 默认带窗口，登录页/风控验证都发生在人眼前；调试完想安静点设 BROWSER_HEADLESS=true。
    browser_headless: bool = False

    session_timeout_minutes: int = 60  # 会话超时（引擎重置运行态）
    # 打招呼演练开关：True 时只导航详情页并定位「立即沟通」，不点任何按钮、不发消息。
    # 要真实发送时在配置文件里设 GREET_DRY_RUN=false（点「立即沟通」本身就会建会话，所以必须整段跳过）。
    greet_dry_run: bool = True
    storage_root: str = ""       # 相对值按形态解析：桌面进 app_home/storage，dev 相对当前 cwd
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _finalize(self):
        desktop = is_desktop()

        # 密钥按 bootstrap.find_secret 那条顺序取，两种形态共用：
        # 哪一处是空的都不算配了，接着往下找，不让一行空占位挡住真值
        self.llm_api_key = bootstrap.resolve_secret(LLM_KEY_NAME, self.llm_api_key)
        self.qianfan_api_key = bootstrap.resolve_secret(QIANFAN_KEY_NAME, self.qianfan_api_key)
        if not desktop and not self.llm_api_key:
            raise ValueError("LLM_API_KEY 未配置：dev 写进仓库 .env，桌面进页面「模型设置」")
        # 桌面缺 LLM key 不拦启动：拦了页面就打不开，用户没法自己补。
        # 真正要用时 llm_client 会抛 LLM_KEY_MISSING，前端引导去「模型设置」。

        # 相对 storage 路径钉成绝对：桌面归运行目录，dev 保持相对 cwd 的历史行为
        root = Path(self.storage_root) if self.storage_root else storage_dir()
        self.storage_root = str(root if root.is_absolute() else Path.cwd() / root)

        if not self.data_base_url:
            self.data_base_url = f"sqlite+aiosqlite:///{db_file().as_posix()}"
        return self


settings = Settings()

if __name__ == '__main__':
    print(settings.llm_base_url)
    print("mode=desktop" if is_desktop() else "mode=dev", "home=", app_home())
