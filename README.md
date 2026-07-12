<div align="center">

# VulnClaw 🦞

> *AI 驱动的渗透测试 CLI 工具 — 说人话，打漏洞。*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![OpenAI Compatible](https://img.shields.io/badge/API-OpenAI_Compatible-green)](https://platform.openai.com/)
[![MCP](https://img.shields.io/badge/Toolchain-MCP-orange)](https://modelcontextprotocol.io/)
[![PyPI](https://img.shields.io/badge/PyPI-v0.3.3-blueviolet)](https://pypi.org/project/vulnclaw/)
[![Security](https://img.shields.io/badge/Scope-Authorized_Only-red)](#-安全声明)
[![AtomGitStars](https://atomgit.com/Unclecheng-li/VulnClaw/star/badge.svg)](https://atomgit.com/Unclecheng-li/VulnClaw)
<br>

🌐 **English version**: [`README_EN.md`](README_EN.md)

**本项目是可独立运行的 AI 渗透测试 Agent。**
<br>
项目官网：https://unclecheng-li.github.io/vulnclaw.com/
<br>

基于 LLM Agent + MCP 工具链 + 渗透 Skill 编排，
配合 OpenAI / Anthropic / MiniMax / DeepSeek 等兼容模型，
自然语言输入 → 自动完成「信息收集 → 漏洞发现 → 漏洞利用 → 报告生成」全流程。

[快速开始](#快速开始) · [架构](#架构) · [内置 Skill](#内置-skill)

</div>

---

## 它能做什么

输入自然语言，AI 自动执行渗透测试全流程：

```
用户输入：帮我对 http://target.example.com 进行渗透测试

VulnClaw 自动执行：
  Round 1:  信息收集 → 指纹识别、端口扫描、目录枚举
  Round 2:  漏洞发现 → 检测注入点、已知 CVE、配置缺陷
  Round 3:  漏洞利用 → PoC 验证、权限获取
  Round 4:  报告生成 → 结构化报告 + Python PoC 脚本
```

<img width="1148" height="642" alt="image" src="https://github.com/user-attachments/assets/576e1cf6-25da-4969-864b-40e77d020dbf" />

<img width="2529" height="1136" alt="image" src="https://github.com/user-attachments/assets/9612c633-31f3-4062-8f56-ea5b4989fd50" />

适用于已授权的渗透测试、CTF 竞赛、安全教学、红队演练等场景。

---

## 特性

- **目标驱动求解引擎（默认）** — 抛弃固定轮数工作流，以「目标达成 / 探索前沿耗尽 / 安全预算」为终止条件，自动收敛
- **黑板图状态空间搜索** — 把渗透建模为从 origin 向 goal 的搜索：Fact（已确认事实）+ Intent（探索方向），结构上杜绝"原地打转"
- **证据级反幻觉闸门** — 声称的 flag/结论必须在真实工具输出里逐字符出现才被采信，杜绝凭空编造 flag 的假胜利
- **自然语言驱动** — 用人话描述渗透意图，自动识别阶段和工具
- **13 个 LLM Provider** — OpenAI / Anthropic / MiniMax / DeepSeek / 智谱 / Moonshot / 千问 / SiliconFlow / 豆包 / 百川 / 阶跃星辰 / 商汤 / 零一万物，一键切换
- **MCP 工具链** — 4 个 MCP 服务：`fetch` / `memory` 本地实现开箱即用，`chrome-devtools` / `burp` 对接外部 MCP 服务实现浏览器自动化和 HTTP 抓包重放
- **原生流量证据存储** — 按运行内作用域过滤后以追加式 JSONL 索引 + 每请求原始报文落盘于 `evidence/traffic/`，内置 `traffic_list` / `traffic_view` / `traffic_repeat` / `traffic_sitemap` 工具直接读写
- **AI Agent 核心** — OpenAI 兼容协议 + Tool Calling + 自主渗透循环
- **结构化推理 + 自适应反思** — 已知事实/约束/攻击链结构化沉淀；失败自动归类并按 L0-L4 渐进升级 payload 绕过策略
- **漏洞检测插件体系** — 低耦合插件运行时 + 内置只读 Web 插件，结果自动汇入报告链路（`vulnclaw plugins`）
- **23 个渗透 Skill** — 7 核心 + 16 专项 Skill（含 CTF Web/Crypto/Misc、osint-recon、cve-triage、hackerone、secknowledge-skill），含 176 个参考文档
- **编解码/加解密工具** — 29 种操作（Base64/Hex/URL/AES/JWT/Morse 等），LLM 可精确调用，不再靠猜测
- **Python 代码执行** — 内置 `python_execute` 工具，适合 payload 构造和响应解析；当前仍属高风险实验能力，不应视为强隔离沙箱
- **持续性渗透测试** — 周期循环（默认 100 轮/周期 × 10 周期 = 1000 轮），每周期自动生成报告
- **推理过程显示控制** — `think on/off` 一键切换 LLM 思考过程的显示/隐藏
- **沙盒模式提示词** — 解锁 AI 安全测试能力，CTF / 授权渗透场景专用
- **自动报告 & PoC** — 生成结构化 Markdown 报告和可运行的 Python PoC 脚本
- **Web UI 模式** — `vulnclaw web` 启动本地 Web 界面，默认 `127.0.0.1:7788`
- **安全知识库** — 已内置知识库模块与基础种子数据，检索增强正在逐步接入主流程

---

## 快速开始

### 安装

```bash
# 从 PyPI 安装（推荐）
pip install vulnclaw

# 从源码安装
git clone https://github.com/Unclecheng-li/VulnClaw.git
cd VulnClaw
pip install -e .
```

### Docker 运行（可选）

镜像已内置 Web UI 以及默认 MCP 服务所需的运行时（`npx` / `uvx`），所有状态持久化到 `/data` 数据卷。

```bash
cp .env.example .env          # 填入 VULNCLAW_LLM_API_KEY 等
docker compose up --build      # 构建镜像并启动 Web UI
# 打开 http://127.0.0.1:7788
```

也可用纯 docker 运行某条 CLI 命令：

```bash
docker run --rm -it \
  -e VULNCLAW_LLM_API_KEY=sk-your-key-here \
  -v vulnclaw-data:/data \
  vulnclaw:latest scan <target>
```

> ⚠️ 容器内的 `localhost` 指向容器自身。扫描宿主机服务请使用 `host.docker.internal`，扫描其它容器请共享网络并用容器名访问。详见 [DOCKER.md](DOCKER.md)。

### 四步启动

```bash
# 1. 选择提供商（自动填充 Base URL 和模型名）
vulnclaw config provider minimax   (或 openai/anthropic/deepseek/zhipu/moonshot/qwen/siliconflow)

# 1.2（可选）自定义 Base URL 或模型名
vulnclaw config set llm.base_url https://your-own-api.example.com/v1 
vulnclaw config set llm.model your-model-name

# 2. 设置 API Key
vulnclaw config set llm.api_key sk-your-key-here
#    — 或改用 ChatGPT 订阅登录（无需 API Key）：
#      vulnclaw login   （浏览器登录；详见 docs/keyless-auth.md，注意 ToS 风险）

# 3. 默认：打开原 CLI / REPL
vulnclaw

# 4. 可选：打开 TUI 工作台
vulnclaw tui
```

### 环境检查

```bash
vulnclaw doctor
```

输出示例：

```
🦞 VulnClaw 环境检查

  Python: 3.14.4
  Node.js: v24.14.1
  npx: 已安装
  nmap: 已安装

LLM 配置:
  Provider: openai
  Auth Mode: static
  Credentials: configured
  Base URL: https://api.openai.com/v1
  Model: gpt-4o

MCP 服务:
  fetch: 已启用 [P0]
  memory: 已启用 [P0]
  ...

✅ 环境就绪，运行 vulnclaw 开始
```

---

## CLI 命令速查

```bash
$ vulnclaw --help

🦞 VulnClaw — AI-powered penetration testing CLI

 Usage: vulnclaw [OPTIONS] COMMAND [ARGS]...

 Commands:
   run           🚀 一键全流程渗透测试
   persistent    🔄 持续性渗透测试（100轮/周期）
   recon         🔍 仅信息收集阶段
   scan          🔎 执行漏洞扫描阶段
   exploit       💥 执行漏洞利用阶段
   report        📝 从会话记录生成报告
   repl          💬 启动经典 REPL 交互界面
   config        ⚙️  管理配置（set/get/list/provider）
   init          🔧 初始化配置
   doctor        🏥  检查运行环境
   tui           🖥️  打开终端图形化工作台
   web           🌐 启动本地 Web UI
```

| 命令 | 说明 | 示例 |
|------|------|------|
| `vulnclaw` | 默认打开原 CLI / REPL | `vulnclaw` |
| `vulnclaw tui` | 终端图形化工作台 | `vulnclaw tui --target target.com` |
| `vulnclaw repl` | 启动经典 REPL 交互界面 | `vulnclaw repl` |
| `vulnclaw solve <target>` | 目标驱动求解（无固定轮数，拿到目标即停） | `vulnclaw solve target.com --goal "拿到flag"` |
| `vulnclaw run <target>` | 一键全流程渗透（默认走 solve 引擎） | `vulnclaw run 192.168.1.1` |
| `vulnclaw persistent <target>` | 持续性渗透（100轮/周期） | `vulnclaw persistent 192.168.1.1` |
| `vulnclaw recon <target>` | 仅信息收集（不利用漏洞） | `vulnclaw recon target.com` |
| `vulnclaw scan <target>` | 漏洞扫描阶段 | `vulnclaw scan target.com --ports 80,443` |
| `vulnclaw exploit <target>` | 漏洞利用阶段 | `vulnclaw exploit target.com --cve CVE-2024-1234` |
| `vulnclaw report <session>` | 从会话 JSON 生成报告 | `vulnclaw report session_xxx.json` |
| `vulnclaw config set <key> <value>` | 设置配置项 | `vulnclaw config set llm.api_key sk-xxx` |
| `vulnclaw config provider <name>` | 切换 LLM 提供商 | `vulnclaw config provider minimax` |
| `vulnclaw plugins list` | 列出漏洞检测插件 | `vulnclaw plugins list --stage discovery` |
| `vulnclaw plugins info <id>` | 查看插件元信息 | `vulnclaw plugins info builtin.web.headers` |
| `vulnclaw plugins run <id>` | 运行插件（仅分析传入数据） | `vulnclaw plugins run builtin.web.headers --input headers.json` |

---

## 使用方式

### 方式一：CLI / REPL（默认）

```bash
vulnclaw
```

无参数启动会进入 🦞 交互界面，用自然语言对话：

```
🦞 vulnclaw> 对 192.168.1.100 进行渗透测试，这是我授权的靶场

[*] 进入自主渗透模式，按 Ctrl+C 可随时中断
── Round 1 ──
  [+] 目标: 192.168.1.100
  [+] 开放端口: 22, 80, 443, 8080
  [+] Web 指纹: Apache/2.4.62
── Round 2 ──
  [+] 发现 /manager/html (Tomcat Manager)
  [+] 命中 CVE-202X-XXXX: Apache Tomcat 认证绕过
── Round 3 ──
  [+] 漏洞验证成功

🦞 192.168.1.100 | 报告> 生成渗透报告
[+] 报告已保存: ./reports/192.168.1.100_20260418.md
[+] PoC 脚本已保存: ./pocs/CVE-202X-XXXX.py
```

**REPL 内置命令：**

| 命令 | 说明 |
|------|------|
| `target <host>` | 设置渗透测试目标 |
| `status` | 查看当前状态 |
| `tools` | 列出当前可用 MCP 工具 |
| `think on/off` | 切换推理过程显示 |
| `persistent` | 启动持续性渗透测试 |
| `clear` | 清空当前会话 |
| `help` | 显示帮助信息 |
| `exit` / `quit` / `q` | 退出 |

**自动渗透触发：** 输入包含「渗透测试」「找 flag」「爆破」等关键词 + 目标地址时，自动进入多轮自主渗透循环。`Ctrl+C` 随时中断。

### 方式二：TUI 工作台

可选的终端图形化工作台，展示授权目标、检查模式、运行概览、安全边界，让用户先确认范围再启动任务。

```bash
vulnclaw tui
vulnclaw tui --target https://target.example --mode quick --only-port 443
vulnclaw tui --dry-run --target https://target.example --mode deep --only-path /admin
```

常用菜单：
- **菜单 3** — 设置测试范围（主机/端口/路径/允许动作/禁止动作）
- **菜单 7** — 环境诊断入口（完整详情运行 `vulnclaw doctor`）
- **菜单 8** — 模型/API 配置（切换 Provider、Base URL、Model、API Key）

### 方式三：单命令模式

```bash
vulnclaw run 192.168.1.100                    # 一键全流程
vulnclaw recon 192.168.1.100                   # 仅信息收集
vulnclaw scan 192.168.1.100 --ports 80,443     # 漏洞扫描
vulnclaw exploit 192.168.1.100 --cve CVE-2024-1234 --cmd id  # 漏洞利用
vulnclaw report session.json                   # 生成报告
```

### 方式四：持续性渗透

适用于需要长时间深度渗透的场景，以**周期循环**方式运行：

```
┌──────────────────────────────────────────────┐
│  Cycle 1 (100轮) → 自动报告 → 继续          │
│  Cycle 2 (100轮) → 自动报告 → 继续          │
│  ...                                         │
│  直到 Ctrl+C 或达到最大周期数（默认10）      │
└──────────────────────────────────────────────┘
```

```bash
vulnclaw persistent 192.168.1.100              # 默认 100轮/周期 × 10周期
vulnclaw persistent 192.168.1.100 -r 200 -c 5  # 200轮/周期 × 5周期
vulnclaw persistent 192.168.1.100 --no-report   # 不自动生成报告

# TUI 方式
vulnclaw tui --target 192.168.1.100 --mode continuous

# REPL 方式
🦞 vulnclaw> persistent 192.168.1.100
```

**特点：** 跨周期状态保持 / 周期报告 / 灵活中断 / 增量发现 / 可配置

### 方式五：Web UI

通过浏览器操作渗透测试全流程。

```bash
pip install 'vulnclaw[web]'  # 安装 Web 依赖
vulnclaw web                  # 启动（默认 127.0.0.1:7788）
vulnclaw web --port 8080      # 自定义端口
```

> ⚠️ 默认仅绑定本地回环地址。如需远程访问须显式指定 `--host 0.0.0.0 --allow-remote`。

---

## 架构

### 求解引擎

VulnClaw 默认使用**目标驱动求解引擎**（旧版固定轮数引擎可通过 `vulnclaw config set session.engine rounds` 回退）。

**黑板图 + OODA 循环：** 把渗透建模为从 origin（目标）向 goal（拿到 flag / shell / 确认高危漏洞）的有向搜索。

| 原语 | 含义 |
|------|------|
| **Fact** | 已被真实工具输出证实的客观事实（探索的落脚点） |
| **Intent** | 声明的探索方向（尚未执行的一步），从 Fact 出发，结论后产出新 Fact |

```
REASON（读全图）→ 目标达成? / 提出新探索方向 / 不提出
        │
EXPLORE（领一个 Intent）→ 用工具实际执行 → 把确认的结论写回为一个 Fact
        │
终止：目标达成 / 探索前沿耗尽 / 触达安全预算
```

**证据级反幻觉闸门：** 录制所有真实工具输出作为唯一可信证据。声称的 flag/结论必须在真实输出里逐字符出现才被采信，杜绝凭空编造。

**结构化推理 + 自适应反思：** 失败自动归类并按 L0-L4 渐进升级 payload 绕过策略，persistent 模式跨周期保留失败记忆。

### 核心模块

| 模块 | 文件 | 说明 |
|------|------|------|
| **CLI/TUI 入口** | `cli/main.py` + `cli/tui.py` | Typer 命令 + REPL + TUI |
| **Agent 核心** | `agent/core.py` | AgentCore 协调入口 |
| **求解引擎** | `agent/solver.py` + `agent/blackboard.py` | OODA 循环 + Fact/Intent 黑板图 |
| **推理/反思** | `agent/reasoning_state.py` + `reflexion.py` | 结构化事实/约束/攻击链 + L0-L4 升级 |
| **插件体系** | `plugins/` | 低耦合漏洞检测插件运行时 |
| **Skill 调度** | `skills/loader.py` + `dispatcher.py` | 意图动态调度 |
| **MCP 编排** | `mcp/registry.py` + `lifecycle.py` + `router.py` | 服务注册 + 生命周期 + 工具路由 |
| **配置管理** | `config/schema.py` + `settings.py` | Pydantic + YAML + 13 Provider 预设 |
| **报告生成** | `report/generator.py` + `poc_builder.py` | Markdown 报告 + PoC 脚本 |
| **安全知识库** | `kb/store.py` + `retriever.py` | JSON 存储 + CVE/技术/工具检索 |

---

## MCP 工具链

| MCP 服务 | 工具数 | 模式 | 用途 | 状态 |
|---|---|---|---|---|
| fetch | 1 | 本地 (httpx) | HTTP 请求、API 测试 | 开箱即用 |
| memory | 2 | 本地 (JSON) | 上下文记忆、状态持久化 | 开箱即用 |
| chrome-devtools | 31+ | stdio MCP | 浏览器自动化、截图、JS 执行 | 需部署 |
| burp | 多个 | stdio MCP | HTTP 抓包、重放、漏洞扫描 | 需部署 |

> 另有 5 个内置 Agent 工具（`python_execute` + `nmap_scan` + `crypto_decode` + `brute_force_login` + `load_skill_reference`），无需 MCP 即可调用。

<details>
<summary><strong>Chrome DevTools MCP 部署</strong></summary>

**前置条件**: Node.js LTS (v20+) + Chrome 浏览器

```bash
# Step 1: 启动 Chrome 远程调试
# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\tmp\chrome-debug
# Linux/Mac
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug

# Step 2: 启用 VulnClaw 配置
vulnclaw config set mcp.servers.chrome-devtools.enabled true
```

如需指定 Chrome 调试地址，编辑 `~/.vulnclaw/config.yaml`：

```yaml
mcp:
  servers:
    chrome-devtools:
      enabled: true
      transport:
        type: stdio
        command: npx
        args: ["-y", "chrome-devtools-mcp@latest", "--browser-url=http://127.0.0.1:9222"]
```

</details>

<details>
<summary><strong>Burp Suite MCP 部署</strong></summary>

**前置条件**: Java 11+ + Burp Suite Professional

```bash
# Step 1: 克隆并构建
git clone https://github.com/PortSwigger/mcp-server.git burp-mcp
cd burp-mcp
./gradlew embedProxyJar    # Windows: gradlew.bat embedProxyJar

# Step 2: 加载到 Burp Suite → Extensions → Add → Type: Java → 选择 burp-mcp-all.jar

# Step 3: 在 Burp 的 MCP 标签页勾选 "Enabled"

# Step 4: 启用 VulnClaw 配置
vulnclaw config set mcp.servers.burp.enabled true
```

建议配置：

```yaml
mcp:
  servers:
    burp:
      enabled: true
      transport:
        type: stdio
        command: java
        args: ["-jar", "~/.vulnclaw/tools/burp-mcp-all.jar", "--sse-url", "http://127.0.0.1:9876"]
```

</details>

> 详细部署说明参见 [docs/mcp-deployment.md](docs/mcp-deployment.md)

---

## 内置 Skill

### 核心 Skill (7)

| Skill | 说明 |
|-------|------|
| pentest-flow | 渗透测试全流程编排 |
| recon | 信息收集流程 |
| vuln-discovery | 漏洞发现流程 |
| exploitation | 漏洞利用流程 |
| post-exploitation | 后渗透流程 |
| reporting | 报告生成流程 |
| waf-bypass | WAF 绕过技巧库 |

### 专项 Skill (16)

| Skill | 参考文档数 | 说明 |
|-------|-----------|------|
| web-pentest | 3 | Web 应用渗透 |
| android-pentest | 9 | 安卓应用渗透 |
| client-reverse | 20 | 客户端逆向分析 |
| web-security-advanced | 33 | Web 安全进阶（注入、绕过、利用链） |
| ai-mcp-security | 7 | AI/MCP 安全测试 |
| intranet-pentest-advanced | 15 | 内网渗透进阶 |
| pentest-tools | 16 | 渗透工具速查 |
| rapid-checklist | 2 | 快速检查清单 |
| crypto-toolkit | 3 | 编解码/加解密（29 种操作） |
| **ctf-web** | 8 | CTF Web 攻击知识库 |
| **ctf-crypto** | 6 | CTF 密码学攻击知识库 |
| **ctf-misc** | 6 | CTF 杂项知识库 |
| **osint-recon** | 7 | OSINT 开源情报收集 |
| **cve-triage** | 1 | CVE 查询与三级评估 |
| **hackerone** | 1 | HackerOne 赏金 scope-guard |
| **secknowledge-skill** | 39 | Web+AI 安全测试知识库 |

Skill 会根据用户输入自动调度，无需手动选择。专项 Skill 含 `references/` 目录下的详细方法论文档，LLM 可通过 `load_skill_reference` 工具按需加载。

### 内置编解码/加解密工具 (crypto_decode)

| 类别 | 操作 |
|------|------|
| 编解码 | base64, base32, base58, hex, url, html, unicode, rot13, caesar, morse（各有 encode/decode） |
| 哈希 | md5, sha1, sha256, sha512 |
| 加解密 | aes_encrypt, aes_decrypt（CBC 模式，PKCS7 填充） |
| JWT | jwt_decode, jwt_encode |
| 自动识别 | auto_decode — 尝试所有常见编码，返回匹配结果 |

---

## 配置管理

### LLM 提供商

```bash
vulnclaw config provider --list    # 查看所有提供商
vulnclaw config provider minimax   # 一键切换
```

| 提供商 | 命令 | 默认模型 |
|--------|------|----------|
| OpenAI | `provider openai` | gpt-4o |
| Anthropic Claude | `provider anthropic` | claude-sonnet-5 |
| MiniMax | `provider minimax` | MiniMax-M3 |
| DeepSeek | `provider deepseek` | deepseek-v4-pro |
| 智谱 GLM | `provider zhipu` | glm-4.7 |
| Kimi | `provider moonshot` | kimi-k2.6 |
| 通义千问 | `provider qwen` | qwen3-max |
| SiliconFlow | `provider siliconflow` | DeepSeek-V4-Flash |
| 豆包 | `provider doubao` | Doubao-Seed-2.0-Pro |
| 百川 | `provider baichuan` | Baichuan4-Turbo |
| 阶跃星辰 | `provider stepfun` | step-3.5-flash |
| 商汤 | `provider sensetime` | SenseNova-6.7-Flash-Lite |
| 零一万物 | `provider yi` | yi-lightning |
| 自定义 | `provider custom` | 手动填写 |

### 命令行配置

```bash
vulnclaw config list                          # 查看所有配置
vulnclaw config get llm.model                 # 查看单项
vulnclaw config set llm.api_key sk-xx         # 设置 API Key
vulnclaw config set session.max_rounds 30     # 设置最大轮数（默认 15）
vulnclaw config set session.show_thinking false # 隐藏推理过程
```

### 可配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `llm.provider` | openai | LLM 提供商 |
| `llm.api_key` | 空 | API Key |
| `llm.auth_mode` | static | `static` 或 `oauth` |
| `llm.chatgpt_auto_proxy` | false | 自动启动内置 ChatGPT 后端桥接代理 |
| `llm.base_url` | 按 provider | API 基础 URL |
| `llm.model` | 按 provider | 模型名称 |
| `llm.temperature` | 0.1 | 采样温度 |
| `llm.max_tokens` | 4096 | 单次最大输出 token |
| `session.engine` | solve | `solve`（目标驱动）/ `rounds`（旧固定轮数） |
| `session.solve_max_steps` | 40 | solve 探索步数安全上限 |
| `session.solve_max_intents` | 3 | 每次 Reason 最多提出的新探索方向数 |
| `session.solve_max_tool_rounds` | 6 | 每个 Intent 探索的最大工具调用轮数 |
| `session.max_rounds` | 15 | 最大轮数 |
| `session.output_dir` | ./vulnclaw-output | 报告输出目录 |
| `session.report_format` | markdown | 报告格式（markdown / html） |
| `session.poc_language` | python | PoC 生成语言（python / bash） |
| `session.show_thinking` | false | 显示 LLM 推理过程 |
| `session.persistent_rounds_per_cycle` | 100 | 持续性渗透每周期轮数 |
| `session.persistent_max_cycles` | 10 | 持续性渗透最大周期数（0=无限） |
| `session.persistent_auto_report` | true | 持续性渗透每周期自动生成报告 |
| `session.stale_rounds_threshold` | 5 | 死循环检测阈值 |

### 环境变量

| 变量 | 说明 |
|------|------|
| `VULNCLAW_LLM_PROVIDER` | LLM 提供商名称 |
| `VULNCLAW_LLM_API_KEY` | API Key |
| `VULNCLAW_LLM_AUTH_MODE` | static / oauth |
| `VULNCLAW_LLM_CHATGPT_AUTO_PROXY` | 内置 ChatGPT 代理 |
| `VULNCLAW_LLM_BASE_URL` | API 基础 URL |
| `VULNCLAW_LLM_MODEL` | 模型名称 |
| `VULNCLAW_SESSION_MAX_ROUNDS` | 最大轮数 |
| `VULNCLAW_SESSION_STALE_ROUNDS_THRESHOLD` | 死循环检测阈值 |
| `VULNCLAW_SESSION_REASONING_STATE_ENABLED` | 结构化推理状态开关 |
| `VULNCLAW_SESSION_REFLEXION_ENABLED` | 自适应反思引擎开关 |
| `VULNCLAW_SESSION_REFLEXION_MAX_SAME_VULN_FAILS` | 同类漏洞连败触发反思阈值 |
| `VULNCLAW_SESSION_ESCALATION_MAX_LEVEL` | Payload 升级上限（0-4） |
| `VULNCLAW_SESSION_PLUGIN_RUNTIME_ENABLED` | 插件运行时开关 |
| `VULNCLAW_SESSION_PLUGIN_MAX_REQUESTS_PER_TARGET` | 单目标插件请求预算 |

优先级：**环境变量 > 配置文件 > 内置默认值**

配置文件位于 `~/.vulnclaw/config.yaml`。

---

## 更新日志

完整更新日志见 [CHANGELOG.md](CHANGELOG.md)。

---

## 安全声明

**公开 Alpha 阶段**：VulnClaw 是面向已授权安全测试、CTF、实验环境与可控研究场景的公开
Alpha 软件，不应作为生产环境的安全控制手段或授权机制。使用前请阅读
[SECURITY.md](SECURITY.md)。

VulnClaw 仅用于**已授权的安全测试**。使用本工具前，请确保：

1. 你已获得目标系统的**明确授权**
2. 测试范围已与目标所有者**书面确认**
3. 你遵守当地**法律法规**

未经授权对系统进行渗透测试是违法行为。本工具作者不对滥用行为承担责任。

---

## 许可证

[MIT License](LICENSE)

---

## 加入社区

与更多安全爱好者一起交流、分享与成长

| 社区交流群 | 开发者群聊 |
|:--:|:--:|
| 欢迎加入讨论分享，获取最新产品动态与使用技巧 | 加入我们，参与开源贡献与技术深度探讨 |
| ![VulnClaw 社区交流群](assets/社区交流群.jpg) | ![VulnClaw 开发者群聊](assets/VulnClaw开发者群聊.png) |
| **QQ 群号：954402631** | **QQ 群号：1065858551** |

---

<div align="center">

> 🦞 **VulnClaw** — 让每一次渗透都有章可循。

</div>
