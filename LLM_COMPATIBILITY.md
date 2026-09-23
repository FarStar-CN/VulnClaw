# LLM 接入与升级说明

更新时间：2026-09-23。协议与参数依据核对于 2026-09-22。

OpenAI、智谱 GLM、DeepSeek、Qwen、Kimi 和 custom 默认使用 Responses。其余供应商保留 Chat Completions 基线。可通过 `llm.api_format` 显式选择协议；请求失败不会切换协议、供应商或地址。默认模型保持现有预设，协议支持不代表所有模型和账户均可使用。

## 配置与升级

```yaml
llm:
  provider: openai
  api_format: responses
  base_url: https://api.openai.com/v1
  model: gpt-5.6-sol
  reasoning_effort: xhigh
  temperature: null
  top_p: null
  max_tokens: 4096
```

| 配置 | 默认 | 含义 |
| --- | --- | --- |
| `api_format` | 按供应商 | `responses` 或 `chat_completions` |
| `temperature` | 0.1 | 有限数值 [0, 2]；模型可能有更严格限制；null 不发送 |
| `top_p` | null | 有限数值 [0, 1]；模型可能有开区间或固定值限制 |
| `reasoning_effort` | xhigh | low、medium、high、xhigh、max |
| `max_tokens` | 4096 | 服务端输出限制；总量语义取决于供应商和模型；正整数或 null |
| `responses` | {} | 原生 Responses 参数、工具、缓存和扩展字段 |
| `max_context_tokens` | 128000 | 本地上下文预算，包含输入与预留输出空间 |

普通配置选择器保持 low、medium、high、xhigh、max；原生 `responses.reasoning.effort` 还可表达 none、minimal。供应商或模型不支持某档位时映射到其可用档位并记录诊断；不接受推理控制的模型省略此参数。单次调用的 null、0 和未指定是不同的意图。环境变量和 CLI 使用相同验证规则，拒绝 NaN、Infinity、非法枚举和非正预算。

```bash
vulnclaw config set llm.api_format responses
vulnclaw config set llm.top_p null
vulnclaw config set llm.temperature null
vulnclaw config set llm.reasoning_effort xhigh
```

环境变量沿用 `VULNCLAW_LLM_` 前缀：`API_FORMAT`、`TOP_P`、`TEMPERATURE`、`REASONING_EFFORT`、`MAX_TOKENS`、`RESPONSES`（JSON 对象）。字符串 `null` 用于可空字段，空字符串不覆盖配置。

仅支持 Chat 的模型、代理或账户需要明确配置，例如：

```yaml
llm:
  provider: zhipu
  api_format: chat_completions
  base_url: https://open.bigmodel.cn/api/paas/v4
  model: glm-5.3
  reasoning_effort: high
  temperature: null
  top_p: null
  max_tokens: null
```

GLM 的输出限制现在正常传出，同时标记其总量语义未确认。null 表示不发送服务端输出上限；本地上下文与任务用量预算仍保留，不能把本地预算等同于服务端计费硬上限。

已有配置不会被静默改写。缺少 `api_format` 的配置按上述供应商默认值运行，首次流式请求的状态信息显示有效协议和地址。升级后以新建会话为验收对象。新历史在检查点中记录格式版本和供应商、模型、协议、端点身份；已有历史时改变身份会要求新建会话。旧数据保留，不尝试跨协议转换旧推理历史。

## 供应商规则与验证状态

| 组合 | 默认地址 | 参数适配 | 离线状态 | 真实端点 |
| --- | --- | --- | --- | --- |
| OpenAI / gpt-5.6-sol | `https://api.openai.com/v1` | `reasoning.effort` 与默认 `summary=auto`；按型号映射档位；开启推理时省略采样；输出上限映射到 `max_output_tokens` | SDK 请求、SSE、工具续接已覆盖 | 未实测 |
| GLM / glm-5.3 | `https://open.bigmodel.cn/api/v1` | 发送 effort，medium→high、xhigh→max；采样与预算正常传出并标记未确认 | 请求路径、参数传出、模拟 SSE 已覆盖 | 未实测，不能据此证明 GLM 参数支持 |
| DeepSeek / deepseek-v4-pro、deepseek-flash | `https://api.deepseek.com` | minimal→low、medium/xhigh→high；推理时省略 temperature、top_p 至少 0.95；none 模式允许 temperature、top_p 固定 1 | SDK 请求、SSE、工具续接已覆盖 | 未实测 |
| Qwen / 文档列出的 Responses 型号 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | Qwen3.8 的 high/max→xhigh；所有型号 temperature < 2、top_p > 0；输出限制至少 16 | SDK 请求、SSE、工具续接已覆盖 | 未实测 |
| Kimi / kimi-k3 | `https://api.moonshot.cn/v1` | medium→high，xhigh→max；不发送采样；其他 Kimi 模型须明确选 Chat | SDK 请求、SSE、工具续接已覆盖 | 未实测 |

映射是应用行为或已注明的供应商别名，不意味着每个原始档位都原生生效。第三方代理上的同名模型使用通用协议字段并标记未验证，不套用官方端点的保证。

GLM 仅对同域名的官方 `/api/paas/v4` 在 Responses 模式下映射为 `/api/v1`；国内与国际域名不互换，Coding 专用路径不自动改写。基址不得带 `/responses` 或 `/chat/completions` 后缀。Qwen 废弃的 `/api/v2/apps/protocols/compatible-mode/v1` 路径会得到迁移提示；业务空间专属 `.maas.aliyuncs.com` 域名可配置。

Qwen3.8 的 `max_output_tokens` 包含推理与正文；部分其他型号只限制正文。现在仍传出输出限制并记录预算语义诊断，不能承诺推理和正文合计受相同上限控制。未知自定义端点采用通用字段及原生扩展透传，不宣称服务端已经实施预算。

依据：[OpenAI Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses)、[OpenAI 推理历史](https://developers.openai.com/api/docs/guides/reasoning)、[GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)、[GLM 国内](https://docs.bigmodel.cn/cn/coding-plan/tool/codex)、[GLM 国际](https://docs.z.ai/devpack/tool/codex)、[DeepSeek Responses](https://api-docs.deepseek.com/api/create-response/)、[DeepSeek Chat](https://api-docs.deepseek.com/api/create-chat-completion/)、[Qwen Responses](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses)、[Kimi Responses](https://platform.kimi.com/docs/api/responses)。

## 原生能力入口

`llm.responses` 接受 JSON/YAML 对象，字段名与 Responses API 一致。调用 `build_request`、`complete` 或 `lifecycle.submit` 时也可传 `response_options={...}` 或原生关键字。优先级为配置 < response_options < 原生关键字；显式的 temperature、top_p、max_tokens、reasoning_effort 参数优先于同名原生控制。reasoning 与 text 在字段层合并，include 与默认加密推理请求合并，工具列表与应用函数工具合并且校验重名。

```yaml
llm:
  responses:
    reasoning:
      effort: high
      summary: auto
    text:
      verbosity: low
    tools:
      - type: web_search
```

此例适用于接受这些能力的 OpenAI 型号。工具不会默认开启；换供应商时须选择该端点支持的工具。可用以下方式保存原生配置：

```bash
vulnclaw config set llm.responses '{"reasoning":{"effort":"none"},"text":{"verbosity":"low"}}'
```

| 能力 | 已实现的接入行为 |
| --- | --- |
| 参数 | 完整标准 Responses create 字段；结构化输出、缓存、服务等级、截断、并行工具等；Qwen OCR/思考扩展与 Kimi 缓存选项 |
| 输入输出 | 文本、图片、文件及原生输入项；保留完整 output、annotations、托管工具产物、OCR 结果、压缩项和原始 usage；通过 Result.raw 读取完整响应 |
| 流 | 原生事件通过可选 sink.on_response_event 回调传递；文字与推理回调保留；去重、终态一致性检查和中断关闭沿用统一逻辑 |
| 函数与自定义工具 | strict、namespace、custom 原始输入、defer_loading、客户端工具搜索，以及每种调用对应的结果格式 |
| 本地原生工具 | shell、local_shell、apply_patch 进入现有执行审批；custom apply_patch 支持新增、更新、移动、删除文件；不加入 computer use |
| 托管工具 | OpenAI 搜索、文件搜索、代码解释器、图像生成、MCP、托管 shell/工具搜索等原生定义与产物；Qwen 搜索、网页抽取、代码解释器、MCP、文件搜索及图片搜索；Kimi 搜索；按各家工具类型规则发送 |
| 状态管理 | lifecycle 中 submit、retrieve、cancel、delete、list_input_items、compact、count_input_tokens、resume_stream；无状态端点拒绝状态操作，未提供相应接口的供应商拒绝相关方法 |

这里的“并集”指统一接入层可以表达每家的能力，不表示每家都支持表内所有功能。OpenAI 和自定义端点支持标准字段及扩展透传；GLM 转发原生参数但保留未验证诊断。DeepSeek、Qwen、Kimi 根据已记录的接口字段适配。明确不支持的可选控制会省略并记录诊断；会改变上下文或任务含义的 previous_response_id、conversation、background、工具类型及 Kimi 非 auto 的 tool_choice 会明确报错，避免生成语义变化。不进行失败后的删参重试或协议回退。

DeepSeek 和 Kimi 为无状态接口；DeepSeek 的 custom 及 Kimi 的 custom/namespace 中自定义工具目前仅接受 apply_patch。Kimi 的 include 仅保留搜索来源/结果。Qwen OCR 用于 qwen3.5-ocr，结果可从 Result.raw 读取。托管工具由供应商执行，其结果不会进入本地工具执行器。原生非 apply_patch 自定义工具和命名空间函数须有应用侧注册的执行工具；客户端 tool_search 按 query 或 goal 检索当前 Agent 可用的工具。协议接入不包含新建文件库、上传文件、创建容器等独立服务的管理界面。

Agent 仍负责本地完整历史、上下文预算、执行轮次和重试，不接受 background、previous_response_id 或 conversation；这些功能通过独立 lifecycle 调用使用。Agent 的 max_tokens 预算优先于 responses.max_output_tokens，配置中的原生工具定义也计入上下文估算。后台调用需保存返回的 response_id；resume_stream 显式使用 starting_after 游标，不自动重放请求或执行工具。

新增本地工具面对的具体风险是模型生成的 shell/补丁绕过审批或改写工作目录之外的文件。shell 复用已有执行器；补丁先检查路径、内容与角色，再提交完整操作给现有 ExecutionGate，批准后复查文件是否变化，写入失败则恢复已写文件。子 Agent 不获得补丁执行权限。服务端 MCP 审批请求同样通过 ExecutionGate 决定并写回原生 approval_response。拒绝、异常和取消都有对应工具结果，已完成结果不会因后续取消丢失。

工具搜索协议依据：[OpenAI 工具搜索](https://developers.openai.com/api/docs/guides/tools-tool-search)。其他字段依据见上方供应商文档；模型和账户可用性以各家实际服务为准。

## 历史、工具与错误行为

- 历史由共享 Agent 循环写入一次，保存原始 message、reasoning、phase、encrypted_content、原生工具调用与对应结果；展示文本不反向构造历史。
- Agent 使用本地完整历史；独立接入调用可显式使用 previous_response_id 或 conversation。OpenAI 请求加密推理内容并设置 store=false；已知不接受这些字段的供应商不发送它们。
- 只有 completed 终态才能执行工具。截断、失败、取消和无终态 EOF 不伪造成功；部分正文另存中断记录，不提交为已完成历史。
- 每个 call_id 都有结果。相同参数去重、超过批次限制、拒绝和取消均保留对应结果；非法 JSON 不修补后执行。
- 历史压缩和归档保留完整关联组，不截断工具参数或加密推理。无法容纳完整组时明确报上下文不足。
- SDK 自动重试关闭。应用仅在同一路由内重试可恢复故障，400/403/404/405/422、参数错误和本地依赖错误直接失败；配额耗尽不会无限重试。
- 生成流握手阶段可以重试；开始消费事件后不自动重放。已执行工具的后续生成失败不会重跑工具。
- 用量保留供应商原始分类；推理与缓存分类不重复加进总量。缺失用量按已有预算机制估算，明确的零值保留。
- 结构化输出按能力选择 json_schema、json_object 或文本加本地解析；只有明确的格式不支持错误触发有限降级，网络、鉴权和截断不触发降级。

## OAuth 与探测

ChatGPT 订阅通过原生 Responses 代理转发 SSE，不再做 Chat 往返转换或缓冲后模拟流。订阅后端不支持应用要求的输出预算字段，自动省略输出上限并记录诊断；采样参数也会省略。过期令牌按已有机制刷新，401 额外刷新并重试一次，第二次拒绝直接返回。

Agent、摘要、报告、结构化辅助调用、L3 扫描、经验提炼、模型发现和 doctor 统一使用 `vulnclaw.llm`。模型发现复用有效端点与凭据；Web 和原生 TUI 复用保存凭据时检查请求地址范围。

```bash
vulnclaw doctor --probe-llm
```

探测会发出两轮无害 ping 工具交换，采用低 effort 并尊重 null 预算。单次未观察到工具调用不等同于供应商不支持工具。

## 接入层目录与职责

接入代码集中在 `vulnclaw/llm/`：

| 模块 | 职责 |
| --- | --- |
| `client.py` | 客户端创建、默认请求头、请求调度及流式调用 |
| `capabilities.py`、`options.py` | 供应商能力、核心参数与原生选项适配 |
| `types.py` | 原始响应、统一结果、用量、工具结果与协议异常 |
| `lifecycle.py` | 创建、查询、取消、删除、输入列表、压缩、输入计数与显式续流 |
| `adapters/responses.py`、`adapters/chat_completions.py` | 两种显式协议的请求转换与响应收集 |
| `token_provider.py`、`chatgpt_proxy.py` | API Key 与 OAuth 凭据管理；ChatGPT 订阅代理 |
| `discovery.py`、`probe.py` | 模型列表发现；连通性与工具调用探测 |

配置模型、供应商预设及配置读写留在 `vulnclaw/config/`，基址校验由其中的 `url_utils.py` 提供。Agent 的上下文预算、工具循环和重试编排留在 Agent 层。对应的协议、凭据、代理和探测测试集中在 `tests/llm/`；Agent 集成测试继续留在 `tests/agent/`。

新增 `llm.responses` 可选配置和 `VULNCLAW_LLM_RESPONSES` 环境变量；配置、OAuth 令牌与会话文件位置不变，无需迁移用户数据。原生工具在现有历史格式内保存，旧版本可能无法继续使用含新增工具的历史。

## Agent 与原 Chat 协议的能力对照

2026-09-23 审查以同一 Agent 运行时、相同工具注册和角色权限下的 Chat Completions 为基准。对照的是应用行为，供应商对模型、采样或托管工具的限制仍按上文处理。Responses 优先、显式 Chat、不自动回退的规则保持不变。

| 原有 Agent 行为 | Responses 验证与修复 |
| --- | --- |
| 内置工具、MCP、子 Agent 工具及角色过滤 | 对比全部主/子角色，两种协议的函数名称、参数 schema 和描述一致，没有因切换协议删减工具 |
| 函数工具同步/流式执行、失败回传 | 用实际 SDK 和模拟 HTTP/SSE 对照；普通 shell 函数返回文本证据，原生 shell 返回专属结构，避免把对象误送入函数结果字段；每次工具只执行一次 |
| 会话检查点与后续请求 | 两种协议均验证保存、加载及继续构造请求；Responses 保留原生调用、结果和推理项 |
| 长任务中的多轮工具循环 | 按完整的一轮模型调用/工具结果分组，裁剪旧轮次时保留最新完整交换；并行调用、推理和 MCP 审批不会被拆开 |
| 手动压缩与历史摘要 | 兼容内容块、函数结果和原生 shell 输出；摘要优先保留工具观察结果，不把加密推理当作正文 |
| 系统提示与上下文预算 | 原生 instructions 追加到 Agent 系统提示，不覆盖它；额外提示预留输入空间，extra_body 中的工具与提示也进入相同预算处理 |
| 原生补丁拒绝与执行结果 | 角色拒绝和审批拒绝显式传递失败状态；保持当前角色权限及 ExecutionGate 行为 |

上述基线之上，保留前述原生输入输出、工具、流式事件等扩展。服务端后台状态与续流仍由独立 lifecycle 接口使用；Agent 自己管理完整历史和工具循环。未加入 computer use，也没有因协议升级放宽执行权限。对照用例见 `tests/agent/test_protocol_parity.py`，原有取消、重试、OAuth、预算和原生工具测试继续执行。

## 验证与维护

2026-09-23 Agent 协议能力对照验收：全量 pytest **1994 passed、5 skipped**；Ruff 通过；执行边界检查 **25 个既有进程启动点全部通过**，本轮未增加或扩大执行许可。1 条 Pydantic 警告来自故意写入非法类型的配置校验用例。未修改前端 TypeScript 或 Rust 源码。

SDK 最低版本为 `openai>=3.13.0`，离线测试使用该版本。契约测试使用虚构密钥和模拟 HTTP/SSE；本轮没有使用用户凭据执行供应商实测。真实账户的访问权限、采样统计效果以及 GLM 的完整契约仍须分别验证，不能从其他供应商的通过结果推断。

```bash
.venv/bin/python -m pytest tests/llm tests/agent tests/config tests/report -q
.venv/bin/python -m pytest -q
make lint PYTHON=.venv/bin/python
.venv/bin/python scripts/verify_execution_boundary.py
```

能力规则集中在 `vulnclaw/llm/capabilities.py` 与 `options.py`。更新时同时记录官方来源和对应 wire 测试；不通过自动换协议、换域名或不断删除参数来掩盖不兼容。
