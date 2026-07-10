# VulnClaw 项目结构文档

> AI 驱动的渗透测试 CLI 工具 | 版本: 0.3.3

## 目录概览

```
VulnClaw/
├── vulnclaw/              # Python 主包
├── frontend/              # React 前端 (Web UI)
├── tests/                 # 测试套件
├── scripts/               # 构建/发布脚本
├── docs/                  # 文档
├── assets/                # 静态资源
├── .github/               # GitHub Actions 工作流
├── pyproject.toml         # Python 项目配置
├── Dockerfile             # Docker 构建文件
├── docker-compose.yml     # Docker Compose 配置
├── Makefile               # 构建命令
└── conftest.py            # pytest 配置
```

---

## 1. `vulnclaw/` - Python 主包

核心业务逻辑，包含 Agent 引擎、CLI、配置管理、MCP 工具链等。

```
vulnclaw/
├── __init__.py
├── orchestrator.py        # 任务编排器
├── repl_runner.py         # REPL 运行器
│
├── agent/                 # Agent 核心引擎
│   ├── core.py            # AgentCore 协调入口
│   ├── solver.py          # 目标驱动求解引擎 (OODA 循环)
│   ├── blackboard.py      # 黑板图状态空间 (Fact/Intent)
│   ├── reasoning_state.py # 结构化推理状态
│   ├── reflexion.py       # 自适应反思引擎 (L0-L4 升级)
│   ├── memory.py          # Agent 记忆引擎
│   ├── parallel_agents.py # 多 intent 并行探索
│   ├── anti_loop.py       # 反死循环检测
│   ├── ctf_mode.py        # CTF 模式状态机
│   ├── llm_client.py      # LLM 客户端 (OpenAI 兼容)
│   ├── prompts.py         # 动态提示词生成
│   ├── system_prompt.py   # System Prompt 组装
│   ├── prompt_context.py  # Round Context 组装
│   ├── input_analysis.py  # 用户输入分析
│   ├── context.py         # 会话状态管理
│   ├── agent_context.py   # Agent 上下文协议
│   ├── skill_context.py   # Skill 选择与注入
│   ├── kb_context.py      # 知识库 Prompt 注入
│   ├── constraint_policy.py # 动作约束策略
│   ├── runtime_state.py   # 运行时状态
│   ├── loop_controller.py # 循环控制器
│   ├── think_filter.py    # 推理过程过滤器
│   ├── token_counter.py   # Token 计数器
│   ├── tool_call_manager.py # 工具调用管理器
│   ├── builtin_tools.py   # 内置工具 (python_execute, nmap 等)
│   ├── recon_tools.py     # 信息收集工具集
│   ├── recon_tracker.py   # 信息收集追踪
│   ├── network_scan.py    # 网络扫描工具
│   ├── finding_parser.py  # 漏洞发现解析器
│   ├── finding_similarity.py # 漏洞相似度去重
│   ├── tool_schemas.py    # 工具 Schema 构建
│   └── chatgpt_proxy.py   # ChatGPT 代理桥接
│
├── cli/                   # CLI 入口
│   ├── main.py            # Typer 命令定义 (vulnclaw 命令)
│   ├── tui.py             # TUI 工作台入口
│   ├── tui_textual.py     # Textual TUI 实现
│   ├── manual.py          # 手册/帮助
│   ├── _helpers.py        # CLI 辅助函数
│   └── textui/            # 终端 UI 组件
│
├── config/                # 配置管理 & 共享层
│   ├── schema.py          # Pydantic 配置模型
│   ├── settings.py        # YAML 配置持久化
│   ├── token_provider.py  # Token 提供者
│   ├── domain_models.py   # 核心领域模型 (VulnerabilityFinding/PentestPhase 等)
│   ├── url_utils.py       # URL 工具函数
│   ├── text_utils.py      # 文本处理工具 (strip_think_tags 等)
│   ├── llm_utils.py       # LLM 工具函数 (build_chat_completion_kwargs 等)
│   ├── finding_similarity.py # 漏洞相似度去重工具
│   └── cli_constants.py   # CLI 常量定义
│
├── mcp/                   # MCP 工具链编排
│   ├── registry.py        # MCP 服务注册
│   ├── lifecycle.py       # MCP 生命周期管理
│   ├── router.py          # 自然语言→工具路由
│   ├── _probe_mixin.py    # 传输探测 (stdio/SSE/HTTP)
│   ├── diagnostics.py     # MCP 诊断信息
│   └── schemas.py         # MCP 数据模型
│
├── plugins/               # 漏洞检测插件体系
│   ├── base.py            # 插件基类
│   ├── registry.py        # 插件注册表
│   ├── runtime.py         # 插件运行时
│   ├── integration.py     # 插件集成
│   ├── result.py          # 插件结果模型
│   └── web/               # 内置 Web 插件
│       ├── headers.py     # 安全响应头检测
│       ├── jwt.py         # JWT 安全检测
│       └── js_endpoints.py # JS 端点分析
│
├── skills/                # Skill 体系
│   ├── loader.py          # Skill 加载器
│   ├── dispatcher.py      # Skill 调度器
│   ├── crypto_tools.py    # 编解码/加解密工具 (29 种)
│   ├── flag_skills.py     # CTF Flag 相关 Skill
│   ├── core/              # 核心 Skill (7 个)
│   │   ├── pentest-flow/
│   │   ├── recon/
│   │   ├── vuln-discovery/
│   │   ├── exploitation/
│   │   ├── post-exploitation/
│   │   ├── reporting/
│   │   └── waf-bypass/
│   └── specialized/       # 专项 Skill (15 个)
│       ├── web-pentest/
│       ├── android-pentest/
│       ├── client-reverse/
│       ├── web-security-advanced/
│       ├── ai-mcp-security/
│       ├── intranet-pentest-advanced/
│       ├── pentest-tools/
│       ├── rapid-checklist/
│       ├── crypto-toolkit/
│       ├── cve-triage/
│       ├── ctf-web/
│       ├── ctf-crypto/
│       ├── ctf-misc/
│       ├── osint-recon/
│       └── secknowledge-skill/
│
├── report/                # 报告生成
│   ├── generator.py       # Markdown 报告生成器
│   ├── poc_builder.py     # Python PoC 脚本生成
│   ├── pdf_exporter.py    # PDF 导出 (可选)
│   ├── filter.py          # 报告过滤器
│   └── verifier.py        # 报告验证器
│
├── web/                   # Web UI 后端
│   ├── app.py             # FastAPI 应用
│   ├── schemas.py         # API 数据模型
│   ├── stream.py          # SSE 流式推送
│   ├── task_manager.py    # 任务管理器
│   ├── services/          # 业务服务层
│   └── static/            # 前端静态文件
│
├── kb/                    # 安全知识库
│   ├── store.py           # JSON 存储
│   ├── retriever.py       # CVE/技术/工具检索
│   └── updater.py         # 知识库更新
│
├── intel/                 # 情报分析模块
│   ├── attack.py          # 攻击分析
│   ├── compliance.py      # 合规检查
│   ├── cve.py             # CVE 数据处理
│   ├── findings.py        # 漏洞发现管理
│   ├── osint.py           # OSINT 开源情报
│   ├── remediation.py     # 修复建议（引擎 + 数据模型）
│   ├── remediation_rules.py # 修复规则定义
│   ├── tools.py           # 情报工具
│   └── topology.py        # 网络拓扑分析
│
├── i18n/                  # 国际化
│   ├── __init__.py
│   ├── en.json            # 英文
│   └── zh.json            # 中文
│
├── target_state/          # 目标状态管理
│   ├── store.py           # 同目标成果沉淀/恢复/快照
│   └── planner.py         # 目标计划器
│
└── warstories/            # 战例库
```

---

## 2. `frontend/` - React 前端

Web UI 前端，基于 React + TypeScript + Vite。

```
frontend/
├── index.html             # HTML 入口
├── package.json           # Node.js 依赖
├── package-lock.json      # 依赖锁文件
├── vite.config.ts         # Vite 配置
├── tsconfig.json          # TypeScript 配置
├── tsconfig.app.json      # App TS 配置
├── tsconfig.node.json     # Node TS 配置
├── public/                # 公共静态资源
└── src/                   # 源代码
    ├── main.tsx           # React 入口
    ├── App.tsx            # 根组件
    ├── styles.css         # 全局样式
    ├── api/               # API 调用层
    ├── components/        # UI 组件
    ├── hooks/             # React Hooks
    ├── i18n/              # 前端国际化
    ├── pages/             # 页面组件
    ├── types/             # TypeScript 类型
    └── utils/             # 工具函数
```

---

## 3. `tests/` - 测试套件

```
tests/
├── __init__.py
├── conftest.py            # pytest fixtures
├── test_basic.py          # 基础测试
├── test_agent.py          # Agent 核心测试
├── test_solver.py         # 求解引擎测试
├── test_blackboard.py     # 黑板图测试
├── test_reflexion.py      # 反思引擎测试
├── test_kb.py             # 知识库测试
├── test_mcp.py            # MCP 测试
├── test_cli.py            # CLI 测试
├── test_web.py            # Web UI 测试
├── test_report.py         # 报告生成测试
├── test_config.py         # 配置管理测试
├── test_plugin_*.py       # 插件体系测试
├── test_skill*.py         # Skill 测试
├── test_recon_*.py        # 信息收集测试
├── test_token_*.py        # Token 相关测试
├── test_llm_*.py          # LLM 客户端测试
├── test_stream_*.py       # 流式传输测试
├── test_release*.py       # 发布流程测试
└── intel/                 # 情报模块测试
```

---

## 4. `scripts/` - 构建脚本

```
scripts/
├── release_preflight.py   # 发布前检查
└── verify_dist_artifacts.py # 分发产物验证
```

---

## 5. `docs/` - 文档

```
docs/
└── mcp-deployment.md      # MCP 部署指南
```

---

## 6. `.github/` - GitHub 配置

```
.github/
├── ISSUE_TEMPLATE/        # Issue 模板
└── workflows/
    ├── ci.yml             # CI 流水线
    ├── release.yml        # 发布工作流
    └── release-preflight.yml # 发布前检查
```

---

## 7. 根目录配置文件

| 文件 | 说明 |
|------|------|
| `pyproject.toml` | Python 项目元数据、依赖、构建配置 |
| `Dockerfile` | Docker 镜像构建 |
| `docker-compose.yml` | Docker Compose 编排 |
| `Makefile` | 构建/测试/发布命令 |
| `.env.example` | 环境变量示例 |
| `.gitignore` | Git 忽略规则 |
| `.dockerignore` | Docker 忽略规则 |
| `.editorconfig` | 编辑器配置 |
| `conftest.py` | pytest 根配置 |
| `LICENSE` | MIT 许可证 |
| `README.md` | 项目说明 (中文) |
| `README_EN.md` | 项目说明 (英文) |
| `CONTRIBUTING.md` | 贡献指南 |
| `CODE_OF_CONDUCT.md` | 行为准则 |
| `SECURITY.md` | 安全声明 |
| `DOCKER.md` | Docker 使用说明 |
| `version-manifest.txt` | 版本清单 |

---

## 8. 核心架构图

```
┌─────────────────────────────────────────────────────────────┐
│                      VulnClaw CLI/TUI                       │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ 自然语言  │  │  任务编排器   │  │    报告 & PoC 生成    │  │
│  │  交互层   │  │ orchestrator │  │      report/          │  │
│  └─────┬────┘  └──────┬───────┘  └───────────┬───────────┘  │
│        └───────────────┼─────────────────────┘              │
│                  ┌─────▼──────┐                              │
│                  │ Agent 核心  │                              │
│                  │  agent/    │                              │
│                  └─────┬──────┘                              │
│        ┌───────────────┼───────────────┐                    │
│   ┌────▼────┐    ┌─────▼─────┐   ┌─────▼─────┐             │
│   │ 求解引擎 │    │  MCP 编排  │   │ 插件运行时 │             │
│   │ solver   │    │   mcp/    │   │ plugins/  │             │
│   └────┬────┘    └─────┬─────┘   └─────┬─────┘             │
│        │               │               │                    │
│   ┌────▼────┐    ┌─────▼─────┐   ┌─────▼─────┐             │
│   │ 黑板图   │    │ 4 个 MCP  │   │ Web 插件  │             │
│   │Fact/Intent│   │  服务     │   │ 安全检测  │             │
│   └─────────┘    └───────────┘   └───────────┘             │
│                                                             │
│   ┌─────────────────────────────────────────────────────┐   │
│   │              Skill 体系 (22 个)                      │   │
│   │  7 核心 Skill + 15 专项 Skill + 编解码工具           │   │
│   └─────────────────────────────────────────────────────┘   │
│                                                             │
│   ┌─────────────────────────────────────────────────────┐   │
│   │              安全知识库 (kb/)                         │   │
│   │  CVE 检索 + 技术文档 + 工具速查                      │   │
│   └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
   ┌──────────┐        ┌──────────┐        ┌──────────┐
   │ LLM API  │        │ MCP 服务 │        │ 目标系统 │
   │ (13 提供商)│       │ (fetch/  │        │ (渗透测试)│
   │          │        │ memory/  │        │          │
   │          │        │ chrome/  │        │          │
   │          │        │ burp)    │        │          │
   └──────────┘        └──────────┘        └──────────┘
```

---

## 9. 技术栈

### 后端 (Python)
- **Python**: 3.10+
- **CLI 框架**: Typer + Rich + prompt_toolkit
- **TUI 框架**: Textual
- **Web 框架**: FastAPI + Uvicorn (可选)
- **LLM SDK**: OpenAI (兼容协议)
- **数据模型**: Pydantic v2
- **配置管理**: PyYAML + TOML
- **模板引擎**: Jinja2
- **HTTP 客户端**: httpx
- **构建工具**: Hatchling

### 前端 (TypeScript)
- **框架**: React
- **构建工具**: Vite
- **类型系统**: TypeScript

### 基础设施
- **容器化**: Docker + Docker Compose
- **CI/CD**: GitHub Actions
- **代码质量**: Ruff (lint) + pytest (测试)

---

## 10. 入口点

| 入口 | 说明 |
|------|------|
| `vulnclaw.cli.main:app` | CLI 主入口 (Typer) |
| `vulnclaw web` | Web UI 启动 |
| `vulnclaw tui` | TUI 工作台 |

---

## 11. 可选依赖

| 额外依赖 | 安装命令 | 说明 |
|----------|---------|------|
| `web` | `pip install vulnclaw[web]` | FastAPI + Uvicorn |
| `kb` | `pip install vulnclaw[kb]` | ChromaDB 向量数据库 |
| `pdf` | `pip install vulnclaw[pdf]` | ReportLab PDF 导出 |
| `dev` | `pip install vulnclaw[dev]` | pytest + ruff + build |

---

## 12. MCP 服务

| 服务 | 模式 | 工具数 | 用途 |
|------|------|--------|------|
| `fetch` | 本地 (httpx) | 1 | HTTP 请求 |
| `memory` | 本地 (JSON) | 2 | 上下文记忆 |
| `chrome-devtools` | stdio MCP | 31+ | 浏览器自动化 |
| `burp` | stdio MCP | 多个 | HTTP 抓包重放 |

---

*文档生成时间: 2026-07-07*
