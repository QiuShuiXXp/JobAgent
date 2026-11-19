# JobAgent

求职智能体：上传简历 → 确认求职意向 → 扫码登录 BOSS 直聘 → LLM 筛岗 → 用户勾选确认 → 自动打招呼。

对话式交互，业务流程由 YAML 编排（`flow_config/`），岗位匹配与意图规划交给 LLM，
浏览器自动化负责登录、检索与打招呼。桌面端一个窗口两块视图：右边聊天，左边内嵌的 BOSS 直聘页面。

> **免责声明**：本项目仅供学习与个人求职辅助使用。自动化操作可能违反目标平台的服务条款，
> 由此产生的账号风险由使用者自行承担。打招呼默认处于演练模式（`GREET_DRY_RUN=true`，只导航不发消息）。

## 架构

```
desktop/            Tauri 桌面壳（Rust）：拉起后端，双 webview 布局，CDP 接管内嵌 WebView2
jobagent/           FastAPI 后端
  api/              路由（聊天 / 设置）与装配
  engine/           对话引擎：规划 → 流程执行 → 回复
  task/             流程编排（YAML 加载、门禁、动作）与后台打招呼任务
  matching/         LLM 批量岗位打分
  knowledge/        知识问答（百度千帆 AI 搜索 MCP，可选）
  infrastructure/   浏览器、SQLite、LLM 客户端、日志、风控
web/                前端页面（Vue 3 CDN 版）——桌面壳与浏览器 dev 形态共用这一套 UI
flow_config/        业务流程与系统流程定义
scripts/            桌面初始化 / 拉取 dev 浏览器 / 一键打包
tests/              单元与 API 测试
```

两种运行形态共用同一套后端与前端：

- **桌面形态**：Tauri 壳拉起后端，BOSS 页面跑在壳内嵌的 WebView2 里，后端经 CDP 接管；
  数据落 `%LOCALAPPDATA%\JobAgent\`（SQLite、简历、日志），密钥进 Windows 凭据管理器。
- **dev 形态**：仓库里直接跑后端，浏览器打开页面调试；后端自己拉一个本机 Chrome
  （可用 `scripts/fetch_browser.py` 备一份 Chrome for Testing）。

## 快速开始（dev 形态）

要求：Python 3.14+、[uv](https://docs.astral.sh/uv/)、Windows（桌面壳依赖 WebView2）。

```bash
uv sync                          # 安装依赖
cp .env.example .env             # 填入 LLM_MODEL / LLM_BASE_URL / LLM_API_KEY
uv run python jobagent/main.py   # 起后端，浏览器打开 http://127.0.0.1:8712
```

没有本机 Chrome 时先跑 `uv run python scripts/fetch_browser.py` 拉一份内置浏览器。

## 桌面形态

```bash
cd desktop
npm install
npm run dev                      # tauri dev：壳自动用仓库 .venv 拉起后端
```

需要 Rust 工具链。壳找不到后端时会显示兜底页并提示看日志。
首次启动自动建运行目录与配置，LLM key 在页面「模型设置」里填即可。

一键出绿色分发包（免 Python、免浏览器，解压即用）：

```bash
uv run python scripts/make_dist.py
```

## 配置

见 [.env.example](.env.example)。要点：

- `LLM_API_KEY`：dev 必填；桌面端可后补（页面「模型设置」）。
- `DATA_BASE_URL`：留空默认本地 SQLite。
- `GREET_DRY_RUN`：默认 `true`（演练，不真发消息）；确认要真实发送再设 `false`。
- `QIANFAN_API_KEY`：可选，配置后启用知识问答。

## 测试

```bash
uv run pytest
```

## License

[MIT](LICENSE)