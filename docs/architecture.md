# JobAgent 架构图

> 由 AI 分析 `jobagent/` 源码自动生成（Mermaid）。可直接在 GitHub/GitLab/VS Code 预览渲染。

## 1. 系统总览

```mermaid
flowchart TB
    subgraph Client["客户端"]
        Desktop["桌面壳<br/>(Tauri / WebView2)"]
        Web["Web 前端<br/>(web/index.html + chat.js)"]
    end

    subgraph API["FastAPI 后端  jobagent/api"]
        Router["路由层<br/>chat_router / settings_router"]
        Middleware["Trace 中间件<br/>correlation_id"]
        Errors["全局异常处理"]
    end

    subgraph Engine["对话引擎  jobagent/engine"]
        EngineCore["DialogueEngine<br/>一轮对话顶层调度"]
        Planner["TurnPlanner<br/>LLM 规划每轮意图"]
        Clarify["ClarifyResponser<br/>追问补槽位"]
        Validator["TurnPlanValidator<br/>校验计划"]
        Knowledge["KnowledgeHandler<br/>知识/FAQ 应答"]
    end

    subgraph Task["任务执行  jobagent/task"]
        TaskHandler["TaskHandler"]
        CmdProc["CommandProcessor<br/>流程状态机命令"]
        FlowExec["FlowExecutor<br/>流程编排"]
        TaskHandler["TaskHandler<br/>命令应用+流程推进+动作分派"]
        Builtin["内置动作<br/>listen / response / save_intent / save_resume"]
        Customer["客户域动作<br/>login / search_jobs / send_greet / logout"]
    end

    subgraph Services["业务服务"]
        Resume["ResumeService<br/>简历解析(LLM+模板)"]
        Match["MatchService<br/>岗位匹配规则"]
        Greet["GreetExecutor + TaskManager<br/>打招呼后台任务"]
    end

    subgraph Infra["基础设施  jobagent/infrastructure"]
        Browser["BrowserManager<br/>(DrissionPage 自动化)"]
        LLM["LLM 客户端<br/>(LangChain + OpenAI 兼容网关)"]
        DB[("数据库<br/>SQLAlchemy / SQLite")]
        FileStore["FileStore<br/>简历文件落盘"]
        Risk["风控纯函数<br/>验证码/封禁判定"]
    end

    subgraph External["外部"]
        BOSS["BOSS直聘 网页"]
        LLMGW["LLM 网关"]
    end

    Desktop --> Web
    Web -- "HTTP /api/*" --> Router
    Router --> EngineCore
    Middleware --> Router
    EngineCore --> Planner
    EngineCore --> Clarify
    EngineCore --> Validator
    EngineCore --> Knowledge
    Planner & Clarify & Knowledge --> LLM
    LLM --> LLMGW
    EngineCore --> TaskHandler
    TaskHandler --> CmdProc
    TaskHandler --> FlowExec
    FlowExec --> TaskHandler
    TaskHandler --> Builtin
    TaskHandler --> Customer
    Customer -- "自动化操作" --> Browser
    Browser --> BOSS
    Browser --> Risk
    Resume --> LLM
    Resume --> FileStore
    Match --> DB
    EngineCore --> Resume
    EngineCore --> Match
    TaskHandler --> Greet
    Greet --> Browser
    CmdProc --> DB
    DB --> DB
```

## 2. 核心对话链路（一轮消息）

```mermaid
sequenceDiagram
    participant U as 用户(前端)
    participant R as 路由 chat_router
    participant E as DialogueEngine
    participant P as TurnPlanner
    participant L as LLM 网关
    participant T as TaskHandler
    participant F as FlowExecutor
    participant A as TaskHandler
    participant B as BrowserManager
    participant D as 数据库

    U->>R: 发送消息
    R->>E: handle_message(UserMessage)
    E->>E: _prepare_session / begin_turn

    alt 文本消息
        E->>P: plan_turn()
        P->>L: 规划本轮意图/槽位
        L-->>P: TurnPlan(commands + flow)
        E->>E: validator 校验 + clarify 追问
    else 对象消息(文件/附件)
        E->>E: _hand_object 直接分派
    end

    E->>T: handle(commands, state)
    T->>T: CommandProcessor 变更状态机
    T->>F: execute_flow()
    F->>A: 逐步执行动作
    A->>B: 浏览器动作(登录/筛岗/打招呼)
    B-->>A: 页面结果
    A-->>F: BotMessage 结果
    F-->>T: 本轮消息列表
    T-->>E: messages
    E->>D: 提交本轮 turn(session/state)
    E-->>R: BotMessage 列表
    R-->>U: 渲染回复
```

## 3. 模块依赖说明

| 层 | 目录 | 职责 |
|----|------|------|
| API | `jobagent/api` | FastAPI 工厂、路由、异常、静态资源 |
| 引擎 | `jobagent/engine` | DialogueEngine 顶层调度 |
| 规划 | `jobagent/plan` | TurnPlanner 意图规划 + 校验 |
| 任务 | `jobagent/task` | 状态机 + 流程编排 + 动作执行 |
| 服务 | `jobagent/services` | 简历解析、岗位匹配、打招呼后台任务 |
| 领域 | `jobagent/domain` | 消息、状态、上下文数据模型 |
| 基础设施 | `jobagent/infrastructure` | 浏览器、LLM、数据库、文件、风控、日志 |
| 知识 | `jobagent/knowledge` | 知识库应答 |
| 提示词 | `jobagent/prompts` | Jinja2 模板加载与自检 |
