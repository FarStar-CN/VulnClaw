# 更新日志

---

<details open>
<summary><strong>Unreleased</strong> — model-led solve engine refactor</summary>

- **重构默认 solve 引擎为模型主导模式** — 删除旧 `ResearchState` / 研究方向 / plan-action-observe 生命周期；solve 现在像 Claude Code/Codex 一样由模型自行决定下一步、工具调用、`FINAL:` 完成、`ASK_USER:` 询问或 `NO_PATH:` 终止。
- **新增 `AgentState` 证据记忆** — 工具结果统一写入 `AgentState.evidence`，默认完整回传给模型；新增 `evidence_list` / `evidence_view` 让模型按需回看历史证据。
- **移除工具后强制二次总结** — solve 工具调用后不再追加 `Summarizing...` LLM 请求，改为确定性工具摘要 + 下一轮模型基于 AgentState 继续，减少 LLM 调用次数和重复试错。
- **新增轻量纠偏层** — 记录工具耗时、失败降级、重复调用和新发现信号，作为 AgentState prompt hint 提供给模型；纠偏层不做阶段规划、不主动安排工具。
- **新增高信号证据固定** — 从工具原始输出中提取源码 SQL、HTML 表单/input、PHP/API 链接和 JavaScript endpoint 构造，写入长期可见 pinned facts，避免后续 HTTP 试错把真实入口淹没；SQL 源码场景会提示模型优先从服务端表达式推导 payload，并在 comment 结尾失败时尝试 no-comment 小步变体。
- **新增 `http_probe_batch` 内置工具** — 一次比较多组 URL/参数/header/body/raw URL 变体，返回状态码、长度、hash、title、关键 body 信号、完整 body 和 same-body 分组；`max_body_chars` 只有显式设为正数时才裁剪。
- **增强 `fetch` 本地工具** — 默认 GET，支持 HTTP/HTTPS、自定义 method/headers/params/cookies/body/data/form/json、timeout/follow_redirects/verify_tls/max_body_chars；默认返回完整响应 body，CTF/靶场 HTTPS 默认不校验证书，减少模型退回 `python_execute` 手写请求的 token 消耗。
- **工具输出默认完整回传模型** — `AgentState` 不再默认把大工具结果替换成摘要预览，`python_execute` 默认完整返回 stdout/stderr；终端仅做显示层折叠预览，模型上下文和证据仍保留完整内容，显式配置正数上限时仍可主动裁剪。
- **修复终端 payload 渲染崩溃** — 工具输出、工具参数和 solve 观察摘要改为 Rich 纯文本渲染，避免 SQL payload 中的 `[/**/]`、`[xxx]` 被误解析为 Rich markup 标签。
- **修复证据查看空转问题** — `evidence_view` / `evidence_list` 现在会写入 AgentState 工具调用记录；重复读取同一 evidence 覆盖范围会被短路，连续多轮只有证据查看且没有新增 evidence 时触发 stall guard，避免模型把预算耗在反复翻同一批日志上。
- **保留 `python_execute` 原始证据** — `python_execute` 的完整 stdout/stderr 会写入 AgentState 并默认直接回传模型；仅在 `python_execute_max_output_chars` 显式设为正数时裁剪即时显示。
- **保留并强化证据闸门** — `FINAL:` 声称的 flag/结论必须由真实工具输出支撑或引用证据编号；不满足时不会假完成，而是把拒绝原因反馈给模型继续探索。
- **新增 solve 自动复盘报告** — 目标达成后基于 `AgentState` 确定性生成 Markdown 报告并默认打印，包含解题思路、关键证据、复现请求包、curl、响应片段和证据索引；新增 `session.solve_auto_report` / `session.solve_report_show` 配置。
- **上下文压缩改为显式/必要时触发** — solve 默认保留正常历史；仅在上下文接近上限、用户执行 `/compact` 或显式启用自动压缩时压缩。
- **工具改为可用能力清单** — 目录扫描、JS 收集、空间测绘、nmap、skill 读取等只作为工具暴露，框架不再按阶段模板主动安排。
- **工具失败不再打崩 solve** — MCP/browser 初始化失败、AnyIO cancel-scope 清理噪声等会作为工具失败证据返回给模型，模型可继续改用其他工具。
- **进度显示改为 Turn** — CLI 不再显示 `Step x/N`，`solve_max_steps` 明确为防失控安全预算，不作为模型工作流轮数；默认安全预算从 80 提高到 240，避免慢模型在接近答案时被过早截断。
- **导入授权红队 Skill** — 吸收 `codex-redteam-mode` 的授权红队 detail packs 到 `vulnclaw/skills/specialized/`；jailbreak、拒绝绕过、会话 patch 等破限内容未导入。
- **新增 SQL 注入实战知识条目** — 基于 fushuling《SQL注入一命通关!》二次整理 `web-sqli-fushuling-one-pass.md`，并接入 `secknowledge-skill` 路由和内置 KB seed。

</details>

---

<details>
<summary><strong>v0.4.1</strong> — 并行探索 + 记忆引擎 + 信息收集工具链 + MCP streamable-http</summary>

- **多方向并行探索** — solve 引擎支持同时探索多个方向（默认 max_parallel=3），单个方向异常不影响其他，每个方向有独立的证据缓冲区和工具调用记录。
- **agent 记忆引擎** — 共享研究状态新增工具调用日志（跨方向可见），reason 阶段显式列出已放弃方向并禁止重复提出，explore 上下文带"已执行工具"摘要；checkpoint 机制在图状态没变时跳过 reason 避免空转；已放弃方向做 Jaccard 去重兜底。
- **结论判定优化** — 放宽了"有进展"的标准（发现新接口、确认未授权都算推进），不再轻易丢弃有价值的发现；最后一步增加证据复核，防止误判丢弃实际有数据返回的探索。
- **完成判定否定闸门** — 模型在 complete 字段里写"未达到完成标准"等否定结论时不会再被误判为已完成；显式要求 complete=true 布尔值 + evidence fact 引用。
- **JS 信息收集（js_recon）** — 抓取页面及全部 JS 文件，提取 API 路径 / 关联域名 / 硬编码密钥；动态发现 PascalCase 实体名并与 base path + CRUD 动词排列组合推断隐藏接口；收集到的接口自动做 GET+POST 未授权探测。
- **未授权探测（unauth_test）** — 批量无凭据请求，按状态码/响应体/内容类型判定；支持有/无 token 差分对比确认未授权；自动跳过 delete/save/sms 等破坏性接口。
- **目录枚举（dir_enum）** — 并发字典爆破，带 404 基线与全局伪装 200 识别（随机路径返回 200 自动停止），状态码与响应长度过滤。
- **空间测绘（space_search）** — FOFA / Hunter / Quake / Shodan / ZoomEye / 0.zone 六引擎统一查询，engine=all 时并发查询所有已配置 key 的引擎。
- **子域名枚举（subdomain_enum）** — 空间测绘被动聚合 + 内置字典 DNS 爆破，自动去重。
- **MCP streamable-http 支持** — 支持 Chrome DevTools MCP 等 HTTP 传输的 MCP 服务器；惰性连接（启动时不占 session slot）；首次调用时自动建连 + 工具发现；连接失败降级为 service_unavailable 不影响 solve 循环。
- **Chrome MCP 工具名修正** — 占位工具改为真实 Chrome MCP 工具名（chrome_navigate / chrome_read_page / chrome_pentest_* 等）。
- 工具返回 undefined 标记为失败而非静默成功；事实序号 / 方向序号在 session 恢复后正确续接；新增 ReconConfig 配置区块与 solve_max_parallel 配置项。

</details>

<details>
<summary><strong>v0.4.0</strong> — 核心：自主引擎从「固定轮数工作流」重构为「目标驱动求解」</summary>

- **新增目标驱动求解引擎（默认）** — 基于已验证事实、研究方向与证据记录的计划/行动循环，以「目标达成 / 研究方向耗尽 / 安全预算」为终止条件，结构上杜绝"原地打转"；新增 `vulnclaw solve` 命令，`run`/REPL 自主模式默认改走该引擎（`session.engine=rounds` 可回退旧逻辑）。
- **新增证据级反幻觉闸门** — 录制所有真实工具输出作为唯一可信证据；声称的 flag/完成必须在真实输出里逐字符出现才被采信，否则判定幻觉并继续探索；拿到验证过的 flag 即时收敛。
- **新增结构化推理 + 自适应反思** — 已知事实（带置信度）/约束/攻击链结构化沉淀并注入提示词；失败自动归类并按 L0–L4 渐进升级 payload 绕过策略，persistent 模式跨周期保留失败记忆。
- **新增漏洞检测插件体系** — 低耦合插件运行时 + 内置只读 Web 插件（安全响应头 / JWT / JS 端点），结果可去重合并进 findings 与报告链路；新增 `vulnclaw plugins list/info/run` 命令。
- **修复 #45 工具被误约束** — 动作约束不再把 HTTP 方法（OPTIONS/POST）或使用 `requests` 误判为「利用」；只有实际攻击载荷（SQLi/RCE/路径穿越等）才算 exploit；`load_skill_reference`/`crypto_decode` 等纯本地工具豁免范围约束。
- 新增 `session.engine` / `solve_*` / `reflexion_*` / `plugin_*` 等配置项，均支持环境变量注入。

</details>
