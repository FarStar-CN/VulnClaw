# LLM 接入与升级说明

更新时间：2026-09-22。协议与参数依据核对于 2026-09-20。

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
| `max_tokens` | 4096 | 推理与正文的总输出预算；正整数或 null | 
| `max_context_tokens` | 128000 | 本地上下文预算，包含输入与预留输出空间 |

档位只有 low、medium、high、xhigh、max：不再提供“交由供应商决定”（原 auto，不发送 effort）和“关闭推理”（原 none）两档，也不提供 minimal。模型接受推理参数时一律发送显式档位；不接受时由能力规则省略并给出诊断，不静默降级。单次调用的 null、0 和未指定是不同的意图。环境变量和 CLI 使用相同验证规则，拒绝 NaN、Infinity、非法枚举和非正预算。

```bash
vulnclaw config set llm.api_format responses
vulnclaw config set llm.top_p null
vulnclaw config set llm.temperature null
vulnclaw config set llm.reasoning_effort xhigh
```

环境变量沿用 `VULNCLAW_LLM_` 前缀：`API_FORMAT`、`TOP_P`、`TEMPERATURE`、`REASONING_EFFORT`、`MAX_TOKENS`。字符串 `null` 用于可空字段，空字符串不覆盖配置。

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

GLM 的总输出预算尚未确认，设置正整数会在发送前报错。null 是明确放弃服务端输出硬上限，不能解释为“请求免费”或“由本地截断控制账单”；应用自身的上下文与任务用量预算仍保留。

已有配置不会被静默改写。缺少 `api_format` 的配置按上述供应商默认值运行，首次流式请求的状态信息显示有效协议和地址。升级后以新建会话为验收对象。新历史在检查点中记录格式版本和供应商、模型、协议、端点身份；已有历史时改变身份会要求新建会话。旧数据保留，不尝试跨协议转换旧推理历史。

## 供应商规则与验证状态

| 组合 | 默认地址 | 参数适配 | 离线状态 | 真实端点 |
| --- | --- | --- | --- | --- |
| OpenAI / gpt-5.6-sol | `https://api.openai.com/v1` | `reasoning.effort`；档位超过模型上限时下调到其最高档；推理模型省略采样；输出上限映射到 `max_output_tokens` | SDK 请求、SSE、工具续接已覆盖 | 未实测 |
| GLM / glm-5.3 | `https://open.bigmodel.cn/api/v1` | Responses 详细参数未确认，省略 effort 和采样字段；非空预算预检失败 | 通用 Responses 请求形状、路径、未确认预算的拒绝已覆盖 | 未实测，不能据此证明 GLM 参数支持 |
| DeepSeek / deepseek-v4-pro、deepseek-flash | `https://api.deepseek.com` | medium/xhigh→high；省略 temperature，top_p 下限按 0.95 处理 | SDK 请求、SSE、工具续接已覆盖 | 未实测 |
| Qwen / 文档列出的 Qwen3.8 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | high/max→xhigh；temperature < 2、top_p > 0；总输出预算至少 16 | SDK 请求、SSE、工具续接已覆盖 | 未实测 |
| Kimi / kimi-k3 | `https://api.moonshot.cn/v1` | medium→high，xhigh→max；不发送采样；其他 Kimi 模型须明确选 Chat | SDK 请求、SSE、工具续接已覆盖 | 未实测 |

映射是应用行为或已注明的供应商别名，不意味着每个原始档位都原生生效。第三方代理上的同名模型使用通用协议字段并标记未验证，不套用官方端点的保证。

GLM 仅对同域名的官方 `/api/paas/v4` 在 Responses 模式下映射为 `/api/v1`；国内与国际域名不互换，Coding 专用路径不自动改写。基址不得带 `/responses` 或 `/chat/completions` 后缀。Qwen 废弃的 `/api/v2/apps/protocols/compatible-mode/v1` 路径会得到迁移提示；业务空间专属 `.maas.aliyuncs.com` 域名可配置。

Qwen 文档中部分其他模型的 `max_output_tokens` 只限制正文，无法满足本应用总输出预算契约；这些组合及预算语义未确认的 Chat 组合要求显式 `max_tokens=null`。未知自定义端点只提供通用字段，不宣称服务端已经实施预算。

依据：[OpenAI Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses)、[OpenAI 推理历史](https://developers.openai.com/api/docs/guides/reasoning)、[GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)、[GLM 国内](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.3)、[GLM 国际](https://docs.z.ai/guides/llm/glm-5.3)、[DeepSeek Responses](https://api-docs.deepseek.com/api/create-response/)、[DeepSeek Chat](https://api-docs.deepseek.com/api/create-chat-completion/)、[Qwen Responses](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses)、[Kimi Responses](https://platform.kimi.com/docs/api/responses)。

## 历史、工具与错误行为

- 历史由共享 Agent 循环写入一次，保存原始 message、reasoning、phase、encrypted_content、function_call 与 function_call_output；展示文本不反向构造历史。
- Responses 使用本地完整历史，不依赖 previous_response_id。OpenAI 请求加密推理内容并设置 store=false；已知不接受这些字段的供应商不发送它们。
- 只有 completed 终态才能执行工具。截断、失败、取消和无终态 EOF 不伪造成功；部分正文另存中断记录，不提交为已完成历史。
- 每个 call_id 都有结果。相同参数去重、超过批次限制、拒绝和取消均保留对应结果；非法 JSON 不修补后执行。
- 历史压缩和归档保留完整关联组，不截断工具参数或加密推理。无法容纳完整组时明确报上下文不足。
- SDK 自动重试关闭。应用仅在同一路由内重试可恢复故障，400/403/404/405/422、参数错误和本地依赖错误直接失败；配额耗尽不会无限重试。
- 生成流握手阶段可以重试；开始消费事件后不自动重放。已执行工具的后续生成失败不会重跑工具。
- 用量保留供应商原始分类；推理与缓存分类不重复加进总量。缺失用量按已有预算机制估算，明确的零值保留。
- 结构化输出按能力选择 json_schema、json_object 或文本加本地解析；只有明确的格式不支持错误触发有限降级，网络、鉴权和截断不触发降级。

## OAuth 与探测

ChatGPT 订阅通过原生 Responses 代理转发 SSE，不再做 Chat 往返转换或缓冲后模拟流。订阅后端不支持应用要求的输出预算字段，须设置 `llm.max_tokens=null`；采样参数会省略。过期令牌按已有机制刷新，401 额外刷新并重试一次，第二次拒绝直接返回。

Agent、摘要、报告、结构化辅助调用、L3 扫描、经验提炼、模型发现和 doctor 统一使用 `vulnclaw.llm`。模型发现复用有效端点与凭据；Web 和原生 TUI 复用保存凭据时检查请求地址范围。

```bash
vulnclaw doctor --probe-llm
```

探测会发出两轮无害 ping 工具交换，采用低 effort 并尊重 null 预算。单次未观察到工具调用不等同于供应商不支持工具。

## 验证与维护

2026-09-22 最终验收：全量 pytest **1901 passed、5 skipped**；Ruff 通过；执行边界检查 **25 个现有进程启动点全部通过**。测试中的 1 条 Pydantic 警告来自故意写入非法类型的配置校验用例。未修改前端 TypeScript 或 Rust 源码。

SDK 最低版本为 `openai>=3.13.0`，离线测试使用该版本。契约测试使用虚构密钥和模拟 HTTP/SSE；本轮没有使用用户凭据执行供应商实测。真实账户的访问权限、采样统计效果以及 GLM 的完整契约仍须分别验证，不能从其他供应商的通过结果推断。

```bash
.venv/bin/python -m pytest tests/llm tests/agent tests/config tests/report -q
.venv/bin/python -m pytest -q
make lint PYTHON=.venv/bin/python
.venv/bin/python scripts/verify_execution_boundary.py
```

能力规则集中在 `vulnclaw/llm/capabilities.py`。更新时同时记录官方来源和对应 wire 测试；不通过自动换协议、换域名或不断删除参数来掩盖不兼容。
