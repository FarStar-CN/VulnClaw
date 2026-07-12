<div align="center">

# VulnClaw 🦞

> *AI-Powered Penetration Testing CLI — Speak plainly, find real bugs.*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![OpenAI Compatible](https://img.shields.io/badge/API-OpenAI_Compatible-green)](https://platform.openai.com/)
[![MCP](https://img.shields.io/badge/Toolchain-MCP-orange)](https://modelcontextprotocol.io/)
[![PyPI](https://img.shields.io/badge/PyPI-v0.3.3-blueviolet)](https://pypi.org/project/vulnclaw/)
[![Security](https://img.shields.io/badge/Scope-Authorized_Only-red)](#-security-notice)
[![AtomGitStars](https://atomgit.com/Unclecheng-li/VulnClaw/star/badge.svg)](https://atomgit.com/Unclecheng-li/VulnClaw)
<br>

🌐 **中文版**: [`README.md`](README.md)

**This project is a standalone AI penetration testing Agent.**
<br>
Official Website: https://unclecheng-li.github.io/vulnclaw.com/
<br>

Built on LLM Agent + MCP Toolchain + Pentest Skill orchestration,
compatible with OpenAI / Anthropic / MiniMax / DeepSeek and similar models.
Natural language input → automated "Recon → Vulnerability Discovery → Exploitation → Reporting".

[Quick Start](#quick-start) · [Architecture](#architecture) · [Built-in Skills](#built-in-skills)

</div>

---

## What It Does

Give it a natural language command and watch it run a full pentest:

```
User:   "Run a penetration test on http://target.example.com"

VulnClaw executes:
  Round 1:  Recon → Fingerprinting, port scan, directory enumeration
  Round 2:  Vulnerability Discovery → Injection points, known CVEs, misconfigs
  Round 3:  Exploitation → PoC verification, access obtained
  Round 4:  Reporting → Structured report + Python PoC script
```

<img width="1148" height="642" alt="image" src="https://github.com/user-attachments/assets/576e1cf6-25da-4969-864b-40e77d020dbf" />

<img width="2529" height="1136" alt="image" src="https://github.com/user-attachments/assets/dd49dc35-4082-4328-a521-8b7a92676e70" />

Suitable for authorized pentests, CTF competitions, security training, and red team operations.

---

## Features

- **Goal-Driven Solver Engine (default)** — Abandons fixed-round workflows; terminates on "goal reached / exploration frontier exhausted / safety budget hit", auto-converges
- **Blackboard State-Space Search** — Models pentest as a search from origin to goal: Facts (confirmed truths) + Intents (exploration directions), structurally prevents dead loops
- **Evidence-Level Anti-Hallucination Gate** — Claims about flags/conclusions must appear verbatim in real tool output to be accepted; prevents fabricated flags
- **Natural Language Driven** — Describe your intent in plain English, auto-identifies phases and tools
- **13 LLM Providers** — OpenAI / Anthropic / MiniMax / DeepSeek / Zhipu / Moonshot / Qwen / SiliconFlow / Doubao / Baichuan / StepFun / SenseTime / Yi, one-command switch
- **MCP Toolchain** — 4 MCP services: `fetch` / `memory` run locally out-of-the-box, `chrome-devtools` / `burp` connect to external MCP servers for browser automation and HTTP interception
- **Native Traffic Evidence Store** — In-scope request/response pairs land in an append-only JSONL index under `evidence/traffic/`. Built-in `traffic_list` / `traffic_view` / `traffic_repeat` / `traffic_sitemap` tools read and replay the store
- **AI Agent Core** — OpenAI-compatible protocol + Tool Calling + autonomous pentest loop
- **Structured Reasoning + Adaptive Reflection** — Facts/constraints/attack chains structured and injected into prompts; failures auto-classified with L0-L4 payload escalation
- **Vulnerability Detection Plugin System** — Low-coupling plugin runtime + built-in read-only Web plugins, results auto-merged into reports (`vulnclaw plugins`)
- **23 Pentest Skills** — 7 core + 16 specialized skills (incl. CTF Web/Crypto/Misc, osint-recon, cve-triage, hackerone, secknowledge-skill), 176 reference documents
- **Encode/Decode & Crypto Tools** — 29 operations (Base64/Hex/URL/AES/JWT/Morse etc.), LLM calls them directly, no guessing
- **Python Code Execution** — Built-in `python_execute` tool for payload crafting and response parsing; currently still a high-risk experimental capability, not a strong isolation sandbox
- **Persistent Pentesting** — Cyclic runs (100 rounds/cycle × 10 cycles = 1000 rounds), auto-reports every cycle
- **Thinking Process Control** — `think on/off` toggles LLM reasoning visibility
- **Sandbox Mode Prompting** — Unlocks AI security testing capabilities, for CTF and authorized pentest scenarios
- **Auto Report & PoC** — Generates structured Markdown reports and runnable Python PoC scripts
- **Web UI Mode** — `vulnclaw web` launches a local web interface, default `127.0.0.1:7788`
- **Security Knowledge Base** — Includes KB module and baseline seed data; retrieval augmentation being integrated

---

## Quick Start

### Installation

```bash
# Install from PyPI (recommended)
pip install vulnclaw

# Install from source
git clone https://github.com/Unclecheng-li/VulnClaw.git
cd VulnClaw
pip install -e .
```

### Run with Docker (optional)

The image bundles the Web UI plus runtimes (`npx` / `uvx`) for default MCP servers. All state persists in a `/data` volume.

```bash
cp .env.example .env          # add VULNCLAW_LLM_API_KEY etc.
docker compose up --build      # build the image and start the Web UI
# open http://127.0.0.1:7788
```

Or run a one-off CLI command:

```bash
docker run --rm -it \
  -e VULNCLAW_LLM_API_KEY=sk-your-key-here \
  -v vulnclaw-data:/data \
  vulnclaw:latest scan <target>
```

> ⚠️ `localhost` inside the container refers to the container itself. To scan a host service use `host.docker.internal`. See [DOCKER.md](DOCKER.md).

### Four-Step Launch

```bash
# 1. Select provider (auto-fills Base URL and model name)
vulnclaw config provider minimax   # or openai / anthropic / deepseek / zhipu / moonshot / qwen / siliconflow

# 1.2 (optional) custom Base URL or model name
vulnclaw config set llm.base_url https://your-own-api.example.com/v1
vulnclaw config set llm.model your-model-name

# 2. Set API Key
vulnclaw config set llm.api_key sk-your-key-here
#    — or sign in with ChatGPT subscription (no API key needed):
#      vulnclaw login   (browser sign-in; see docs/keyless-auth.md, note ToS caveat)

# 3. Default: open the original CLI / REPL
vulnclaw

# 4. Optional: open the TUI workbench
vulnclaw tui
```

### Environment Check

```bash
vulnclaw doctor
```

Sample output:

```
🦞 VulnClaw Environment Check

  Python: 3.14.4
  Node.js: v24.14.1
  npx: installed
  nmap: installed

LLM Config:
  Provider: openai
  Auth Mode: static
  Credentials: configured
  Base URL: https://api.openai.com/v1
  Model: gpt-4o

MCP Services:
  fetch: enabled [P0]
  memory: enabled [P0]
  ...

✅ Ready. Run vulnclaw to start.
```

---

## CLI Command Reference

```bash
$ vulnclaw --help

🦞 VulnClaw — AI-powered penetration testing CLI

 Usage: vulnclaw [OPTIONS] COMMAND [ARGS]...

 Commands:
   run           🚀 Full pentest in one shot
   persistent    🔄 Persistent pentesting (100 rounds/cycle)
   recon         🔍 Reconnaissance only
   scan          🔎 Vulnerability scanning
   exploit       💥 Exploitation phase
   report        📝 Generate report from session JSON
   repl          💬 Start the classic REPL
   config        ⚙️  Manage config (set/get/list/provider)
   init          🔧 Initialize configuration
   doctor        🏥  Check runtime environment
   tui           🖥️  Open the terminal UI workbench
   web           🌐 Launch local Web UI
```

| Command | Description | Example |
|---------|-------------|---------|
| `vulnclaw` | Open the original CLI / REPL by default | `vulnclaw` |
| `vulnclaw tui` | Terminal UI workbench | `vulnclaw tui --target target.com` |
| `vulnclaw repl` | Start the classic REPL | `vulnclaw repl` |
| `vulnclaw solve <target>` | Goal-driven solver (no fixed rounds) | `vulnclaw solve target.com --goal "get the flag"` |
| `vulnclaw run <target>` | Full pentest in one shot | `vulnclaw run 192.168.1.1` |
| `vulnclaw persistent <target>` | Persistent pentesting (100 rounds/cycle) | `vulnclaw persistent 192.168.1.1` |
| `vulnclaw recon <target>` | Reconnaissance only | `vulnclaw recon target.com` |
| `vulnclaw scan <target>` | Vulnerability scanning | `vulnclaw scan target.com --ports 80,443` |
| `vulnclaw exploit <target>` | Exploitation phase | `vulnclaw exploit target.com --cve CVE-2024-1234` |
| `vulnclaw report <session>` | Generate report from session JSON | `vulnclaw report session_xxx.json` |
| `vulnclaw config set <key> <value>` | Set a config value | `vulnclaw config set llm.api_key sk-xxx` |
| `vulnclaw config provider <name>` | Switch LLM provider | `vulnclaw config provider minimax` |
| `vulnclaw plugins list` | List vulnerability detection plugins | `vulnclaw plugins list --stage discovery` |
| `vulnclaw plugins info <id>` | View plugin metadata | `vulnclaw plugins info builtin.web.headers` |
| `vulnclaw plugins run <id>` | Run plugin (analysis only) | `vulnclaw plugins run builtin.web.headers --input headers.json` |

---

## Usage

### Mode 1: CLI / REPL (Default)

```bash
vulnclaw
```

No-args startup opens the 🦞 interactive shell for natural-language use:

```
🦞 vulnclaw> pentest 192.168.1.100 — this is my authorized lab

[*] Entering autonomous pentest mode. Press Ctrl+C to interrupt.
── Round 1 ──
  [+] Target: 192.168.1.100
  [+] Open ports: 22, 80, 443, 8080
  [+] Web fingerprint: Apache/2.4.62
── Round 2 ──
  [+] Discovered /manager/html (Tomcat Manager)
  [+] Matched CVE-202X-XXXX: Apache Tomcat Auth Bypass
── Round 3 ──
  [+] Vulnerability verified

🦞 192.168.1.100 | report> generate pentest report
[+] Report saved: ./reports/192.168.1.100_20260418.md
[+] PoC saved: ./pocs/CVE-202X-XXXX.py
```

**REPL Built-in Commands:**

| Command | Description |
|---------|-------------|
| `target <host>` | Set pentest target |
| `status` | View current state |
| `tools` | List available MCP tools |
| `think on/off` | Toggle thinking process display |
| `persistent` | Start persistent pentesting |
| `clear` | Clear current session |
| `help` | Show help |
| `exit` / `quit` / `q` | Exit |

**Auto Pentest Trigger:** Keywords like "pentest", "find flag", "bruteforce" + a target address auto-enter the multi-round autonomous loop. `Ctrl+C` to interrupt anytime.

### Mode 2: TUI Workbench

Optional terminal UI workbench showing authorized target, check mode, runtime overview, and safety boundary — confirm scope before launching.

```bash
vulnclaw tui
vulnclaw tui --target https://target.example --mode quick --only-port 443
vulnclaw tui --dry-run --target https://target.example --mode deep --only-path /admin
```

Common menus:
- **Menu 3** — Set testing scope (host/port/path/allowed actions/blocked actions)
- **Menu 7** — Environment diagnostics (full details via `vulnclaw doctor`)
- **Menu 8** — Model/API settings (switch Provider, Base URL, Model, API Key)

### Mode 3: Single Command

```bash
vulnclaw run 192.168.1.100                    # full pentest
vulnclaw recon 192.168.1.100                   # recon only
vulnclaw scan 192.168.1.100 --ports 80,443     # vuln scan
vulnclaw exploit 192.168.1.100 --cve CVE-2024-1234 --cmd id  # exploit
vulnclaw report session.json                   # generate report
```

### Mode 4: Persistent Pentesting

For long-running deep pentesting. Runs in **cyclic loops**:

```
┌──────────────────────────────────────────────┐
│  Cycle 1 (100 rounds) → auto-report → continue │
│  Cycle 2 (100 rounds) → auto-report → continue │
│  ...                                             │
│  Until Ctrl+C or max cycles reached (default 10) │
└──────────────────────────────────────────────┘
```

```bash
vulnclaw persistent 192.168.1.100              # default 100 rounds/cycle × 10 cycles
vulnclaw persistent 192.168.1.100 -r 200 -c 5  # 200 rounds/cycle × 5 cycles
vulnclaw persistent 192.168.1.100 --no-report   # disable auto-report

# TUI mode
vulnclaw tui --target 192.168.1.100 --mode continuous

# REPL mode
🦞 vulnclaw> persistent 192.168.1.100
```

**Features:** Cross-cycle state / Cycle reports / Graceful interrupt / Incremental discovery / Fully configurable

### Mode 5: Web UI

Operate the full pentest workflow through a browser.

```bash
pip install 'vulnclaw[web]'  # install Web dependencies
vulnclaw web                  # launch (default 127.0.0.1:7788)
vulnclaw web --port 8080      # custom port
```

> ⚠️ By default binds to localhost only. For remote access pass `--host 0.0.0.0 --allow-remote`.

---

## Architecture

### Solver Engine

VulnClaw defaults to the **goal-driven solver engine** (switch back to fixed-round with `vulnclaw config set session.engine rounds`).

**Blackboard + OODA Loop:** Models pentest as a directed search from origin (target) to goal (flag / shell / confirmed high-severity vuln).

| Primitive | Meaning |
|-----------|---------|
| **Fact** | An objective truth confirmed by real tool output (a foothold for exploration) |
| **Intent** | A declared exploration direction (not yet executed), originating from a Fact, producing a new Fact upon conclusion |

```
REASON (read full graph) → Goal reached? / Propose new direction / None
        │
EXPLORE (pick an Intent) → Execute with tools → Write confirmed conclusion back as a Fact
        │
Terminate: Goal reached / Exploration frontier exhausted / Safety budget hit
```

**Evidence-Level Anti-Hallucination Gate:** Records all real tool output as the sole trusted evidence. Claims must appear verbatim in real output to be accepted; fabricated claims are discarded.

**Structured Reasoning + Adaptive Reflection:** Failures auto-classified with L0-L4 payload escalation; persistent mode preserves failure memory across cycles.

### Core Modules

| Module | File | Description |
|--------|------|-------------|
| **CLI/TUI Entry** | `cli/main.py` + `cli/tui.py` | Typer commands + REPL + TUI |
| **Agent Core** | `agent/core.py` | AgentCore coordination entrypoint |
| **Solver Engine** | `agent/solver.py` + `agent/blackboard.py` | OODA loop + Fact/Intent blackboard |
| **Reasoning / Reflection** | `agent/reasoning_state.py` + `reflexion.py` | Structured facts/constraints/attack chains + L0-L4 escalation |
| **Plugin System** | `plugins/` | Low-coupling vulnerability detection plugin runtime |
| **Skill Dispatcher** | `skills/loader.py` + `dispatcher.py` | Intent-based dynamic dispatch |
| **MCP Orchestration** | `mcp/registry.py` + `lifecycle.py` + `router.py` | Service registry + lifecycle + tool routing |
| **Config** | `config/schema.py` + `settings.py` | Pydantic + YAML + 13 provider presets |
| **Report Generator** | `report/generator.py` + `poc_builder.py` | Markdown reports + PoC scripts |
| **Security KB** | `kb/store.py` + `retriever.py` | JSON storage + CVE/technique/tool retrieval |

---

## MCP Toolchain

| MCP Service | Tools | Mode | Use Case | Status |
|---|---|---|---|---|
| fetch | 1 | Local (httpx) | HTTP requests, API testing | Out-of-the-box |
| memory | 2 | Local (JSON) | Context memory, state persist | Out-of-the-box |
| chrome-devtools | 31+ | stdio MCP | Browser automation, screenshots, JS execution | Requires setup |
| burp | Multiple | stdio MCP | HTTP interception, replay, vuln scanning | Requires setup |

> Plus 5 built-in Agent tools (`python_execute` + `nmap_scan` + `crypto_decode` + `brute_force_login` + `load_skill_reference`) — no MCP needed.

<details>
<summary><strong>Chrome DevTools MCP Setup</strong></summary>

**Prerequisites**: Node.js LTS (v20+) + Chrome browser

```bash
# Step 1: Start Chrome with remote debugging
# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\tmp\chrome-debug
# Linux/Mac
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug

# Step 2: Enable in VulnClaw
vulnclaw config set mcp.servers.chrome-devtools.enabled true
```

Custom Chrome debug address — edit `~/.vulnclaw/config.yaml`:

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
<summary><strong>Burp Suite MCP Setup</strong></summary>

**Prerequisites**: Java 11+ + Burp Suite Professional

```bash
# Step 1: Clone and build
git clone https://github.com/PortSwigger/mcp-server.git burp-mcp
cd burp-mcp
./gradlew embedProxyJar    # Windows: gradlew.bat embedProxyJar

# Step 2: Load into Burp Suite → Extensions → Add → Type: Java → select burp-mcp-all.jar

# Step 3: Enable in Burp's MCP tab

# Step 4: Enable in VulnClaw
vulnclaw config set mcp.servers.burp.enabled true
```

Recommended config:

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

> See [docs/mcp-deployment.md](docs/mcp-deployment.md) for detailed setup instructions.

---

## Built-in Skills

### Core Skills (7)

| Skill | Description |
|-------|-------------|
| pentest-flow | Full pentest workflow orchestration |
| recon | Information gathering |
| vuln-discovery | Vulnerability discovery |
| exploitation | Exploitation |
| post-exploitation | Post-exploitation |
| reporting | Report generation |
| waf-bypass | WAF bypass techniques |

### Specialized Skills (16)

| Skill | Ref Docs | Description |
|-------|----------|-------------|
| web-pentest | 3 | Web application pentesting |
| android-pentest | 9 | Android application pentesting |
| client-reverse | 20 | Client-side reverse engineering |
| web-security-advanced | 33 | Advanced web security (injection, bypass, chains) |
| ai-mcp-security | 7 | AI/MCP security testing |
| intranet-pentest-advanced | 15 | Advanced internal network pentesting |
| pentest-tools | 16 | Pentest tool quick reference |
| rapid-checklist | 2 | Rapid validation checklists |
| crypto-toolkit | 3 | Encode/decode/crypto (29 ops) |
| **ctf-web** | 8 | CTF Web attacks (PHP bypass/RCE/SSTI/deserialization) |
| **ctf-crypto** | 6 | CTF cryptography (RSA/AES/ECC/PRNG/lattice attacks) |
| **ctf-misc** | 6 | CTF Misc (PyJail/BashJail/encoding chains/VM RE) |
| **osint-recon** | 7 | OSINT four-dimension model (server/web/domain/person) |
| **cve-triage** | 1 | CVE lookup and triage |
| **hackerone** | 1 | HackerOne bounty scope-guard |
| **secknowledge-skill** | 39 | Web+AI security testing knowledge base |

Skills are auto-dispatched based on user input — no manual selection needed. Specialized skills include detailed methodology documents in `references/`, loadable via the `load_skill_reference` tool.

### Built-in Encode/Decode & Crypto Tool (`crypto_decode`)

| Category | Operations |
|----------|------------|
| Encoding | base64, base32, base58, hex, url, html, unicode, rot13, caesar, morse (each with encode/decode) |
| Hashing | md5, sha1, sha256, sha512 |
| Encrypt | aes_encrypt, aes_decrypt (CBC mode, PKCS7 padding) |
| JWT | jwt_decode, jwt_encode |
| Auto | auto_decode — tries all common encodings, returns matching results |

---

## Configuration

### LLM Providers

```bash
vulnclaw config provider --list    # list all providers
vulnclaw config provider minimax   # one-command switch
```

| Provider | Command | Default Model |
|----------|---------|---------------|
| OpenAI | `provider openai` | gpt-4o |
| Anthropic Claude | `provider anthropic` | claude-sonnet-5 |
| MiniMax | `provider minimax` | MiniMax-M3 |
| DeepSeek | `provider deepseek` | deepseek-v4-pro |
| Zhipu GLM | `provider zhipu` | glm-4.7 |
| Kimi | `provider moonshot` | kimi-k2.6 |
| Qwen | `provider qwen` | qwen3-max |
| SiliconFlow | `provider siliconflow` | DeepSeek-V4-Flash |
| Doubao | `provider doubao` | Doubao-Seed-2.0-Pro |
| Baichuan | `provider baichuan` | Baichuan4-Turbo |
| StepFun | `provider stepfun` | step-3.5-flash |
| SenseTime | `provider sensetime` | SenseNova-6.7-Flash-Lite |
| Yi | `provider yi` | yi-lightning |
| Custom | `provider custom` | manual |

### CLI Configuration

```bash
vulnclaw config list                          # view all settings
vulnclaw config get llm.model                 # view single setting
vulnclaw config set llm.api_key sk-xx         # set API key
vulnclaw config set session.max_rounds 30     # set max rounds (default 15)
vulnclaw config set session.show_thinking false  # hide thinking process
```

### Configurable Options

| Option | Default | Description |
|--------|---------|-------------|
| `llm.provider` | openai | LLM provider |
| `llm.api_key` | empty | API key |
| `llm.auth_mode` | static | `static` or `oauth` |
| `llm.chatgpt_auto_proxy` | false | Auto-start built-in ChatGPT bridge proxy |
| `llm.base_url` | per provider | API base URL |
| `llm.model` | per provider | Model name |
| `llm.temperature` | 0.1 | Sampling temperature |
| `llm.max_tokens` | 4096 | Max output tokens |
| `session.engine` | solve | `solve` (goal-driven) / `rounds` (legacy fixed-round) |
| `session.solve_max_steps` | 40 | Solver exploration step safety cap |
| `session.solve_max_intents` | 3 | Max new exploration directions per Reason |
| `session.solve_max_tool_rounds` | 6 | Max tool call rounds per Intent |
| `session.max_rounds` | 15 | Max rounds |
| `session.output_dir` | ./vulnclaw-output | Report output directory |
| `session.report_format` | markdown | Report format (markdown / html) |
| `session.poc_language` | python | PoC language (python / bash) |
| `session.show_thinking` | false | Show LLM reasoning |
| `session.persistent_rounds_per_cycle` | 100 | Rounds per cycle in persistent mode |
| `session.persistent_max_cycles` | 10 | Max cycles (0=unlimited) |
| `session.persistent_auto_report` | true | Auto-report after each cycle |
| `session.stale_rounds_threshold` | 5 | Dead-loop detection threshold |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `VULNCLAW_LLM_PROVIDER` | LLM provider name |
| `VULNCLAW_LLM_API_KEY` | API key |
| `VULNCLAW_LLM_AUTH_MODE` | static / oauth |
| `VULNCLAW_LLM_CHATGPT_AUTO_PROXY` | Built-in ChatGPT proxy |
| `VULNCLAW_LLM_BASE_URL` | API base URL |
| `VULNCLAW_LLM_MODEL` | Model name |
| `VULNCLAW_SESSION_MAX_ROUNDS` | Max rounds |
| `VULNCLAW_SESSION_STALE_ROUNDS_THRESHOLD` | Dead-loop threshold |
| `VULNCLAW_SESSION_REASONING_STATE_ENABLED` | Structured reasoning toggle |
| `VULNCLAW_SESSION_REFLEXION_ENABLED` | Adaptive reflection toggle |
| `VULNCLAW_SESSION_REFLEXION_MAX_SAME_VULN_FAILS` | Same-vuln failure trigger threshold |
| `VULNCLAW_SESSION_ESCALATION_MAX_LEVEL` | Payload escalation cap (0-4) |
| `VULNCLAW_SESSION_PLUGIN_RUNTIME_ENABLED` | Plugin runtime toggle |
| `VULNCLAW_SESSION_PLUGIN_MAX_REQUESTS_PER_TARGET` | Per-target plugin request budget |

Priority: **Environment Variables > Config File > Built-in Defaults**

Config file: `~/.vulnclaw/config.yaml`.

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for full version history.

---

## Security Notice

**Public alpha:** VulnClaw is public alpha software for authorized security testing, CTFs, labs, and controlled research. It is not a production security control or a replacement for human authorization. See [SECURITY.md](SECURITY.md) before using or reporting security issues.

VulnClaw is intended **solely for authorized security testing**. Before using this tool, ensure:

1. You have **explicit authorization** for the target system
2. Scope has been **confirmed in writing** with the target owner
3. You comply with all applicable **local laws and regulations**

Unauthorized penetration testing is illegal. The author assumes no liability for misuse.

---

## License

[MIT License](LICENSE)

---

## Join the Community

Connect with security enthusiasts to share, learn, and grow together.

| Community Group | Developer Group |
|:--:|:--:|
| Join discussions and get the latest product updates and usage tips | Join us for open-source contributions and deep technical discussions |
| ![VulnClaw Community Group](assets/社区交流群.jpg) | ![VulnClaw Developer Group](assets/VulnClaw开发者群聊.png) |
| **QQ Group: 954402631** | **QQ Group: 1065858551** |

---

<div align="center">

> 🦞 **VulnClaw** — Every pentest should follow a process.

</div>
