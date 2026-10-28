# 无头模式操作指南

## 1. 选择调用方式

| 需求 | 入口 | 状态生命周期 |
| --- | --- | --- |
| CI 扫描、批处理、一次问答、配置和数据管理 | `vulnclaw run/ask/config/...` | 每次调用创建、清理自己的应用会话 |
| 无界面的连续对话、回答追问、逐次审批、运行中控制 | `python -m vulnclaw.session_backend` | 一个进程内，通过 stdin/stdout 交换 v2 JSONL |
| 人工操作的终端界面 | `vulnclaw repl` / `vulnclaw tui` | 终端客户端维持后端会话 |

普通业务命令默认就是无头模式，不需要 `--non-interactive`。
无参数 `vulnclaw` 返回成功的 JSON 命令目录；`--help` 仍显示文本帮助。Python 无头入口无需 Bun/OpenTUI。

一次性 CLI 能执行相同的核心任务，但两次 `ask` 不共享聊天上下文。
`ask '/target ...'`、`ask '/scope ...'`、`ask '/permission ...'` 修改的是本次应用会话，
不能用来配置下一条 shell 命令。任务范围通过本次任务参数传入；跨进程权限通过配置或环境变量设置。
已有运行数据用恢复命令加载，连续交互用会话协议。


面向 Agent 的最小接入顺序：

```bash
vulnclaw                          # 命令目录，退出码 0
vulnclaw config                   # 配置操作目录，退出码 0
vulnclaw describe run             # 实际参数、默认值、约束、JSON 输入模型
vulnclaw describe --protocol      # 当前解释器的启动 argv、初始化顺序、请求类型
vulnclaw describe --protocol approval_required
vulnclaw describe --protocol waiting_for_input
vulnclaw describe --protocol cancel_task
vulnclaw describe --protocol shutdown
```

这些发现入口仅读取接口定义，不启动模型或 MCP。命令目录的 `operations` 按名称排序；
每项提供 `required`、`example_argv` 和 `describe_argv`。具体命令的 `parameters` 区分
`argument` 与 `option`；任务的 `input_requirements.sources` 列出替代输入来源，选择其中一个，
不能重复指定。`input_json_schema` 是现有任务模型，别名可接受的 `command` 见 `input_json_command`。
协议描述通过同样的最终 `command_result.result` 返回；启动后还应核对 `ready.backend`
的版本及 `ready.capabilities.control_operations`，只调用当前后端支持的操作。

迁移变化：根命令与 `config/kb/plugins/experience/target-state/code` 无子命令时，
从缺参/帮助行为改为成功发现；`config set` 从完整配置改为 `{key,value,saved}` 摘要，
完整配置使用 `config list`。协议仍为 v2，既有退出码保持不变，无长期双格式开关。

## 2. 安装、配置与诊断

在仓库根目录执行：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
vulnclaw --version
vulnclaw init
vulnclaw config provider --list
vulnclaw config list
vulnclaw doctor
```

使用环境变量传入模型连接信息，API key 从自己的凭据系统注入：

```bash
export VULNCLAW_LLM_PROVIDER=custom
export VULNCLAW_LLM_BASE_URL='https://your-provider.example/v1'
export VULNCLAW_LLM_MODEL='your-model'
# 在执行环境中设置 VULNCLAW_LLM_API_KEY
vulnclaw doctor --probe-llm > doctor.json
```

检查 `doctor.json` 中的 `result.probe.ok` 和 `result.probe.category`。
诊断命令成功输出结果，不等于模型通过工具调用探测。
配置输出会遮罩密钥；环境变量优先于配置文件。
MCP 服务与相关扫描工具仍需自行配置，`doctor` 和运行状态中的 MCP 就绪信息用于核对连接。

已有 OAuth token bundle 可导入：

```bash
vulnclaw login --token-file /path/to/oauth_tokens.json
vulnclaw logout
```

无头 `login` 不发起浏览器授权；未提供 token bundle 会返回 `requires_input`。

## 3. 执行任务与读取输出

将示例目标替换为已授权目标：

```bash
vulnclaw recon https://authorized.example --no-resume --max-steps 12
vulnclaw scan https://authorized.example --ports 80,443 --max-steps 12
vulnclaw run https://authorized.example --scan-mode standard --max-steps 24
vulnclaw solve https://authorized.example --goal '验证给定问题' --max-steps 24
vulnclaw persistent https://authorized.example --rounds 2 --cycles 1 --max-steps 12
vulnclaw ask '解释已有证据中的风险与需要补充的信息'
```

`recon`、`scan`、`exploit` 是共享 `run` 工作流的动作限制别名。
`persistent` 使用同一工作流的周期执行选项；脚本中应明确设置周期上限。
`quick/standard/deep` 预设控制投入预算，显式 `--max-*` 优先。
`--scope-mode auto` 当前仍按 `full` 执行，不能依此获得 Git diff 增量扫描。

默认 stdout 是一个 `command_result` JSON，日志走 stderr：

```bash
vulnclaw run https://authorized.example --max-steps 12 > result.json 2> run.log
```

需要进度、推理、工具与发现事件时：

```bash
vulnclaw run https://authorized.example --max-steps 12 --format jsonl \
  > events.jsonl 2> run.log
```

每行是一个 v2 JSON 对象；最后恰好一个 `command_result`。
`--stream` 也选择 JSONL。不要混用相互矛盾的输出格式参数。
`message` 事件通过 `message_id`、`operation=append|end` 组合正文；
`tool_call/tool_execution/tool_result` 描述工具执行；
`subagent` 及带 `agent_id` 的事件区分子任务。
一次性无头事件保留完整工具输出；终端后端可能通过 `execution.details.read` 分页读取大字段。

### 结构化任务输入

`task.json`：

```json
{
  "command": "run",
  "target": "https://authorized.example",
  "resume": false,
  "options": {
    "max_steps": 12,
    "allowed_ports": [443],
    "only_path": "/api",
    "blocked_path": "/logout",
    "allow_actions": ["recon", "scan"],
    "auto_report": true
  }
}
```

```bash
vulnclaw run --input-json task.json --format jsonl
cat task.json | vulnclaw run --input-json -
```

JSON 使用任务 DTO 的字段名和类型；`options` 放任务选项。
目标、预算或其他字段若同时在 JSON 与显式 CLI 参数中出现，即使值相同也会报冲突。
`--input-json -` 读取一个完整 JSON 文档后执行，需要发送 stdin EOF。

### 退出码与产物

| 退出码 | 含义 |
| --- | --- |
| 0 | 正常完成，未达到所选发现阻断阈值 |
| 1 | 失败、取消、缺少输入或需要审批；再检查 `status` 和 `error` |
| 2 | `--fail-on verified/any` 下存在当前审计认定的已验证发现 |
| 3 | `--fail-on any` 下只有疑似候选发现 |

默认 `--fail-on verified`，候选不阻断；`--fail-on never` 只取消发现阈值，执行错误仍返回 1。
退出码 0 不表示已证明目标安全，也不表示模型已完成所有研究目标。

在开启 `set -e` 的脚本中，先捕获状态，避免来不及收集报告：

```bash
scan_status=0
vulnclaw run https://authorized.example --fail-on verified > result.json 2> run.log \
  || scan_status=$?
python -m json.tool result.json
exit "$scan_status"
```

从最终结果的 `artifacts`、`result.run.directory`、`result.report` 读取实际文件路径。
任务运行目录包含 `run.json`、目标检查点和 `summary.json`；
自动报告同时导出报告正文、findings JSON 与 SARIF。
`--no-report` 关闭自动报告，任务审计和检查点仍执行。

## 4. 权限、追问、取消与恢复

默认权限是 `ask`。一次性 CLI 遇到审批返回 `requires_approval`，
遇到必需答案返回 `requires_input`，均以 1 退出；不会在 stdin 等待后续交互。
`result.interaction` 包含审批或问题信息。

无人值守任务要事先配置适用的执行策略。例如限定可自动审查的命令前缀：

```bash
vulnclaw config set safety.permission_mode auto_review
vulnclaw config set safety.trusted_commands '["git diff", "git status"]'
```

这不代表所有工具都会自动通过；仍可能返回 `requires_approval`。
逐项审批应使用会话协议。模型的“确认继续”不能代替执行审批。
`read_only` 可用于支持的读取和已有证据分析，会拒绝扫描等执行操作。
若需要只对某次运行覆盖策略，可用实际支持的环境变量：

```bash
VULNCLAW_SAFETY_PERMISSION_MODE=read_only vulnclaw ask '解释已有风险'
```

`full_access` 表示允许以当前用户权限执行工具，须由操作者按任务授权范围明确选用。

恢复已保存运行：

```bash
vulnclaw run https://authorized.example --resume-run RUN_NAME --runs-dir ./runs
vulnclaw --resume RUN_NAME --runs-dir ./runs
```

前者重新进入任务工作流；根级 `--resume` 加载指定检查点，通过继续对话推进。
两者都不等于恢复同一进程的待审批 Future，或完整恢复先前终端聊天上下文。
保存状态损坏且确实需要回退到有效快照时使用 `--repair`。
新任务用 `--no-resume`；指定自定义运行根目录时，恢复也传入同一目录。

SIGINT/SIGTERM 通过异步 SDK 取消当前请求、关闭流和应用资源，再输出唯一的
`command_result(status=cancelled, exit_code=1)`。本地阻塞 HTTP/SSE 验证覆盖响应头、
流中途、重试等待、独立审计和报告摘要，均在 3 秒内退出。检查点可按上面的恢复入口继续。

## 5. 无界面的持续会话

启动：

```bash
python -u -m vulnclaw.session_backend
```

stdin/stdout 使用 [v2 协议](../protocol/session-v2.schema.json)，stderr 是诊断日志。
这是当前 REPL/TUI 的后端协议入口，使用时应绑定版本。
每条请求包含 `protocol_version`、`type`、`request_id`、`payload`；
任务和对话请求另带 `task_id`。先 `initialize` 并等待 `ready`，再发后续请求。

连续两轮问答的最小 Python 客户端：

```python
import json
import subprocess
import sys

process = subprocess.Popen(
    [sys.executable, "-u", "-m", "vulnclaw.session_backend"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    text=True, encoding="utf-8", bufsize=1,
)

def send(kind, request_id, payload=None, task_id=None):
    message = {
        "protocol_version": 2, "type": kind,
        "request_id": request_id, "payload": payload or {},
    }
    if task_id is not None:
        message["task_id"] = task_id
    process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
    process.stdin.flush()

def wait_for(kind, request_id):
    for line in process.stdout:
        event = json.loads(line)
        print(json.dumps(event, ensure_ascii=False))
        if event["type"] in {"error", "turn_failed", "task_failed"}:
            raise RuntimeError(event)
        if event["type"] == kind and event.get("request_id") == request_id:
            return event
    raise RuntimeError("后端在返回预期事件前退出")

try:
    send("initialize", "init")
    wait_for("ready", "init")
    for index, text in enumerate([
        "请记住：本次讨论的是离线代码审查。",
        "我们刚才约定讨论什么？",
    ]):
        identifier = f"turn-{index}"
        send("submit_input", identifier, {"text": text}, task_id=identifier)
        wait_for("turn_completed", identifier)
finally:
    if process.poll() is None:
        send("shutdown", "shutdown")
        process.stdin.close()
        wait_for("shutdown_complete", "shutdown")
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    process.stdout.close()
```

该示例用于普通问答。任务客户端还应处理以下交互事件：

| 操作 | 请求或处理方式 |
| --- | --- |
| 执行结构化任务 | `start_task`，`payload={"task": TASK_DTO}`，等 `task_completed/failed/cancelled` |
| 设置会话范围 | `control`，`operation=session.scope.update`，`arguments={"scope":{"allowed_ports":[443]}}` |
| 读取当前状态 | `get_state`，等待同一 `request_id` 的 `state` |
| 回复任务追问 | 等待状态的 `task.waiting_for_input=true`，再用 `submit_input` 发送答案 |
| 审批工具 | 收到 `approval_required` 后，发送 `control`：`operation=execution.approval.resolve`，`arguments={"request_hash":"事件中的值","decision":"approve或deny"}` |
| 取消当前任务 | `cancel_task` 的 `task_id` 使用当前活动任务 ID；等待取消事件，进程可继续处理下一轮 |
| 读取长工具输出 | `control` 的 `execution.details.read`；按事件提供的引用 ID、字节偏移分页 |
| 结束 | 发送 `shutdown`，关闭 stdin，等待进程退出 |

`control` 请求的 `payload` 包含 `operation` 和 `arguments`，返回 `control_result`。
支持的操作以 `ready.capabilities.control_operations` 为准。
`/target`、`/scope`、`/permission`、`/new`、`/compact`、`/think` 等也可作为 `submit_input.text` 发送。
权限策略、会话范围和任务状态以 Python 返回值为准。
仅当读到 `ready` / 相应完成事件后再发送依赖请求，不要用一次性 shell 管道同时塞入任务与 shutdown。

## 6. 管理、代码扫描与已知差异

```bash
vulnclaw config set session.language zh
vulnclaw config set session.show_thinking true
vulnclaw config set terminal.theme orange
vulnclaw kb status
vulnclaw kb update
vulnclaw plugins list
vulnclaw plugins info PLUGIN_ID
vulnclaw experience list
vulnclaw experience show LESSON_ID
vulnclaw experience approve LESSON_ID
vulnclaw experience edit LESSON_ID --context '适用条件' --lesson '修订经验'
vulnclaw learn RUN_NAME --runs-dir ./runs
vulnclaw feedback RUN_NAME --rating 4 --notes '证据清楚' --runs-dir ./runs
vulnclaw target-state list https://authorized.example
vulnclaw target-state preview https://authorized.example
vulnclaw verify /path/to/session.json --no-poc-verify
vulnclaw report /path/to/session.json
```

`experience list` 独立子命令当前只列待审核项。
查看其他分类用会话协议的 `experience.list`，或
`vulnclaw ask '/experience list --status all'`，从最终 `command_result.result` 读取内容。

不需要模型的本地代码扫描：

```bash
vulnclaw code scan ./src --layer L1,L2 --format sarif --output scan.sarif \
  --output-format json --fail-on any
```

这里 `--format` 是扫描产物格式，`--output-format` 才是 stdout 的 JSON/JSONL 格式。
静态发现是候选，`--fail-on any` 有候选时返回 3。
L3 语义审查另外依赖模型配置，不属于纯离线的 L1/L2 路径。

`ask` 调用控制命令时，其业务结果进入最终 `command_result.result`。
JSONL 同时保留原 `control_result` 事件，两种格式的最终业务结果一致。
`/theme`、`/settings`、`/clear`、`/quit` 属于终端界面操作，不能直接交给 `ask` 执行；
主题和设置通过 `config` 修改，显示清屏、布局、剪贴板及快捷键没有无头交互界面。

## 7. Agent 委托实例：配置、执行、审批、回答与收集报告

以下以授权测试 `https://staging.example/api` 为例，限制在 443 端口的侦察和扫描。
输入可以直接照其结构构造；输出中的 ID、命令、提问和路径仅用于说明，由实际运行产生。
大型事件用字段摘录展示；客户端必须读取完整 JSONL 对象。模型是否发出追问或工具调用取决于任务。

### 7.1 配置模型并确认保存结果

先从凭据系统注入 `VULNCLAW_LLM_API_KEY`，再发送：

```bash
vulnclaw config set llm.provider custom
vulnclaw config set llm.base_url https://your-provider.example/v1
vulnclaw config set llm.model your-model
vulnclaw config set safety.permission_mode ask
vulnclaw doctor --probe-llm
```

以权限配置为例，stdout 是一个完整最终结果，进程退出码 0：

```json
{"protocol_version":2,"type":"command_result","request_id":"command-config-1","status":"completed","exit_code":0,"error":null,"result":{"key":"safety.permission_mode","value":"ask","saved":true}}
```

`config set llm.api_key ...` 的返回 `value` 是 `"***"`，落盘值仍为输入密钥。
环境变量优先：保存新的模型名或密钥后，已有 `VULNCLAW_LLM_MODEL/API_KEY` 仍覆盖保存值。
`doctor` 返回成功只表示诊断完成，还需检查 `result.probe.ok`。配置和会话启动是两个独立阶段。

### 7.2 修正参数错误

例如误输入：

```bash
vulnclaw run --input-json - <<'JSON'
{"target":"https://staging.example/api","options":{"max_steps":"abc"}}
JSON
```

退出码为 1。最终结果中的反馈摘录为：

```json
{"feedback":{"issues":[{"code":"invalid_value","path":["options","max_steps"],"message":"Input should be a valid integer, unable to parse string as an integer"}],"next_actions":[{"action":"inspect_command","describe_argv":["vulnclaw","describe","run"]}]}}
```

Agent 根据 `path` 将字段改为整数，例如 `12`，重新校验 schema 后提交。
缺少目标会给出 `missing_field` 和带目标的命令示例；非法配置项会给出候选名称；
无凭据会给出凭据环境变量和 `login --token-file` 入口。不要依赖英文错误消息的措辞解析错误。

### 7.3 根据发现结果启动持续后端

读取 `vulnclaw describe --protocol` 的 `result.transport.command_argv`，
使用 argv 数组启动子进程，不拼接 shell 命令。stdin/stdout 都保持打开，stderr 单独收集。
向 stdin 写入一行并 flush：

```json
{"protocol_version":2,"type":"initialize","request_id":"init-1","payload":{"client":{"name":"caller-agent","version":"1.0"}}}
```

等待 `type=ready, request_id=init-1`。核对以下字段：

```json
{"backend":{"version":"0.4.1","protocol_version":2},"capabilities":{"natural_language":true,"cancellation":true,"authoritative_state":true,"permission_mode":"ask"}}
```

同时读取完整 `capabilities.control_operations`，确认包括
`execution.approval.resolve`、`report.export` 等即将使用的操作。不要只凭 CLI 版本猜测后端能力。

### 7.4 在同一进程执行具体任务

发送：

```json
{"protocol_version":2,"type":"start_task","request_id":"scan-1","task_id":"scan-task-1","payload":{"task":{"command":"run","target":"https://staging.example/api","resume":false,"prompt":"在授权范围内检查 API 暴露面；发现需要凭据时先询问，不猜测凭据。","options":{"max_steps":12,"allowed_ports":[443],"only_path":"/api","allow_actions":["recon","scan"],"auto_report":false}}}}
```

客户端持续收集 `task_started`、`message`、`tool_call/tool_execution/tool_result`、`finding` 等事件。
消息按 `message_id` 和 `operation=append|end` 组装，工具和子 Agent 按各自 ID 关联。
不能在收到 `task_started` 后停止读取，也不能把工具执行成功直接当成发现已验证。

### 7.5 收到执行审批后按契约回复

`approval_required` 事件的关键字段可能是：

```json
{"request_hash":"hash-from-current-event","kind":"shell","question":"curl -I https://staging.example/api/health","cwd":"/work","detail":"...","expires_at":"2026-10-27T12:00:00+00:00","expires_in_seconds":300,"risk":"..."}
```

Agent 查询 `describe --protocol approval_required`，读取
`response.arguments_schema`、`field_sources` 和 `completion`。按任务授权策略决定是否允许；
需要操作者决定时先将事件交给操作者，拿到决定后再发送。批准请求为：

```json
{"protocol_version":2,"type":"control","request_id":"decision-1","payload":{"operation":"execution.approval.resolve","arguments":{"request_hash":"hash-from-current-event","decision":"approve"}}}
```

`request_hash` 原样复制当前事件，不使用 task_id 或自己计算的哈希。拒绝时只把 `decision` 改为 `deny`。
等待与 `decision-1` 关联的响应：

```json
{"protocol_version":2,"type":"control_result","request_id":"decision-1","operation":"execution.approval.resolve","result":{"status":"resolved"}}
```

只有内层 `result.status=resolved` 表示本次决定已应用；它不表示整个任务完成。
`unknown_request/already_resolved` 不能当成批准成功；非法 `decision=yes` 返回 `error(code=invalid_control)`。
审批回复后继续等待原 `scan-1` 的任务事件。审批控制响应可能与任务后续事件交错，须按 request_id 分发。
一次性 CLI 进程已经退出时，其旧审批哈希失效；恢复检查点后遇到新的审批，应回复新的事件。

### 7.6 回答业务追问并检查阶段状态

模型可能询问“是否有本次授权使用的 API 测试凭据？”。当前阶段会产生 `task_completed`，
其中状态仍可能是：

```json
{"task":{"active":false,"task_id":null,"waiting_for_input":true}}
```

这里的 `task_completed` 表示阶段结束。先读取消息中的问题，按
`describe --protocol waiting_for_input` 的契约，用新请求 ID 和新轮次 task_id 发送：

```json
{"protocol_version":2,"type":"submit_input","request_id":"answer-1","task_id":"answer-turn-1","payload":{"text":"没有提供认证凭据；仅检查公开接口，不提交表单，不尝试认证绕过。"}}
```

等待 `turn_completed(request_id=answer-1)`，继续检查其 `state.task.waiting_for_input`。
若仍为 true，处理下一问题；如果还有审批事件，走审批契约。
业务答案放在 `payload.text`，审批决定放在 `control.payload.arguments`，两者不能互换。
`active=false` 且 `waiting_for_input=false` 表示当前轮已空闲，是否达到委托目标还需结合任务结果和证据判断。

### 7.7 导出报告和读取产物

任务/回答阶段结束后，在同一进程发送：

```json
{"protocol_version":2,"type":"control","request_id":"report-1","payload":{"operation":"report.export","arguments":{"kind":"security"}}}
```

等待 `control_result(request_id=report-1)`，读取 `result.report` 中的实际路径，例如：

```json
{"report":{"kind":"security","path":"/work/runs/run-example/reports/report.md","findings_json":"/work/runs/run-example/reports/report.artifacts/findings/findings.json","findings_sarif":"/work/runs/run-example/reports/report.artifacts/findings/findings.sarif"},"message":"Report saved: /work/runs/run-example/reports/report.md"}
```

这是控制结果的业务内容摘录，实际文件名由导出器决定。发现数量和验证状态以审计产物为准。
一次性任务可改用 `auto_report=true`，随后从唯一最终 `command_result.artifacts` 和 `result.report` 收集文件。
需要查询当前状态时发送：

```json
{"protocol_version":2,"type":"get_state","request_id":"state-1","payload":{}}
```

等待 `state(request_id=state-1)`，核对目标、权限、范围、任务状态及发现。不要从此前显示文本推测当前状态。

### 7.8 取消运行与关闭会话

取消活动任务时，`task_id` 必须是当前活动任务 ID。例如仍在执行 `scan-task-1`：

```json
{"protocol_version":2,"type":"cancel_task","request_id":"cancel-1","task_id":"scan-task-1","payload":{}}
```

等待关联 `cancel-1` 的 `task_cancelled`；普通对话取消则是 `turn_cancelled`。
取消后进程继续可用，可提交下一轮；无需重新初始化。空闲或任务 ID 失配时查看实际返回的状态/错误。
结束委托时发送：

```json
{"protocol_version":2,"type":"shutdown","request_id":"shutdown-1","payload":{}}
```

flush 后关闭 stdin，继续读取到 `shutdown_complete(request_id=shutdown-1)` 和 stdout EOF，
最后等待进程退出。持续后端使用任务/轮次及关闭事件，不产生一次性 CLI 的 `command_result`。
