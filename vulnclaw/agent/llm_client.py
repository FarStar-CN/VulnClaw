"""LLM client helpers for AgentCore."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from vulnclaw.agent.subagent.budget import (
    fail_request as _fail_subagent_llm_admission,
)
from vulnclaw.agent.subagent.budget import (
    fit_messages as _hard_fit_subagent_messages,
)
from vulnclaw.agent.subagent.budget import (
    is_subagent as _is_subagent,
)
from vulnclaw.agent.subagent.budget import (
    prepare_request as _prepare_subagent_llm_request,
)
from vulnclaw.agent.subagent.budget import (
    record_usage as _record_subagent_llm_usage,
)
from vulnclaw.agent.subagent.budget import (
    reserve_request as _reserve_subagent_llm_request,
)
from vulnclaw.agent.subagent.budget import (
    settle_request as _settle_subagent_llm_admission,
)

if TYPE_CHECKING:
    from vulnclaw.agent.agent_context import AgentContext

logger = logging.getLogger(__name__)

from vulnclaw.agent.context_budget import prepare_context  # noqa: E402
from vulnclaw.agent.token_counter import (  # noqa: E402
    estimate_tokens,
    group_tool_exchanges,
    truncate_messages,
)
from vulnclaw.agent.tool_call_manager import handle_tool_calls_with_results  # noqa: E402
from vulnclaw.llm.capabilities import UNSET  # noqa: E402
from vulnclaw.llm.client import (  # noqa: E402
    await_stream_open,
    build_request,
    create,
    stream_result,
)
from vulnclaw.llm.types import ProtocolError, Result  # noqa: E402

_CONTEXT_USABLE_RATIO = 0.9
_DEFAULT_AUTO_TOOL_ROUNDS = 6
_TOOL_LOOP_TARGET_RATIO = 26_000 / 32_000




def _fit_context_window(
    agent: AgentContext,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    purpose: str = "agent",
) -> list[dict[str, Any]]:
    """Fit messages to the configured context window via the unified budget."""
    result = prepare_context(agent, messages, tools, purpose=purpose)
    if result.compacted or result.reason:
        logger.info(
            "Context budget %d -> %d tokens (%s)",
            result.before_tokens,
            result.after_tokens,
            result.reason or "prepared",
        )
    # Subagent hard-fit: subagent budgets are tighter than the LLM window;
    # clamp again when the prepare step left a subagent working set oversized.
    if _is_subagent(agent):
        llm = getattr(agent, "config", None)
        llm = getattr(llm, "llm", None) if llm is not None else None
        max_context = getattr(llm, "max_context_tokens", None)
        if (
            isinstance(max_context, (int, float))
            and not isinstance(max_context, bool)
            and max_context > 0
        ):
            budget = int(max_context * _CONTEXT_USABLE_RATIO)
            if estimate_tokens(result.messages) > budget:
                return _hard_fit_subagent_messages(result.messages, budget)
    return result.messages


def _tool_loop_hot_budget(agent: AgentContext) -> int:
    """Return the high-water mark for one internal tool-loop working set."""
    context = getattr(agent, "context", None)
    budget = getattr(context, "max_tokens", 32_000)
    if not isinstance(budget, (int, float)) or isinstance(budget, bool):
        return 32_000
    return max(1_024, int(budget))


def _tool_loop_target_budget(agent: AgentContext) -> int:
    """Return the post-compaction target, 26K for the default 32K high-water mark."""
    high_water = _tool_loop_hot_budget(agent)
    return min(high_water, max(1_024, int(high_water * _TOOL_LOOP_TARGET_RATIO)))


def _build_tool_loop_messages(
    agent: AgentContext,
    system_prompt: str,
    round_context: str,
    *,
    include_history: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build a stable prefix followed by the mutable internal tool-loop tail.

    The prefix is created once per ``call_llm_auto`` invocation and never
    reordered while its tool loop runs: system prompt, bounded hot history,
    then the current task instruction.  Assistant/tool exchanges are appended
    after it as the mutable tail.  This lets providers reuse the unchanged
    prefix between tool rounds until a high-water compaction is necessary.
    """
    messages = [{"role": "system", "content": system_prompt}]
    if include_history:
        messages.extend(agent.context.get_messages())
    round_message = {"role": "user", "content": round_context}
    messages.append(round_message)
    return messages, round_message


def _fit_tool_loop_context(
    agent: AgentContext,
    messages: list[dict[str, Any]],
    round_message: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compact an oversized tool-loop tail without breaking tool exchanges.

    ``ContextManager`` bounds persisted history, but a single ``call_llm_auto``
    can append several assistant/tool exchanges before it returns.  Compact that
    local working set only after it crosses the high-water mark.  It then falls
    back to a lower target (26K for the default 32K high-water mark), preserving
    the stable system/task prefix and complete recent tool exchanges.
    """
    high_water = _tool_loop_hot_budget(agent)
    if estimate_tokens(messages) <= high_water:
        return messages
    target = _tool_loop_target_budget(agent)

    try:
        round_index = next(
            index for index, message in enumerate(messages) if message is round_message
        )
    except StopIteration:
        return truncate_messages(messages, target, preserve_system=True)

    system_messages = (
        [messages[0]] if messages and messages[0].get("role") == "system" else []
    )
    history_start = len(system_messages)
    history = messages[history_start:round_index]
    tool_loop = messages[round_index + 1 :]
    required = [*system_messages, round_message]
    if estimate_tokens(required) >= target:
        return truncate_messages(required, target, preserve_system=True, min_recent=1)

    running = estimate_tokens(required)

    def keep_recent(
        groups: list[list[dict[str, Any]]],
    ) -> list[list[dict[str, Any]]]:
        nonlocal running
        kept: list[list[dict[str, Any]]] = []
        for group in reversed(groups):
            cost = estimate_tokens(group)
            if running + cost > target:
                break
            running += cost
            kept.insert(0, group)
        return kept

    # Prefer the evidence acquired in this still-active tool loop, then fill
    # remaining space with older persistent history.
    kept_tool_loop = keep_recent(group_tool_exchanges(tool_loop))
    if tool_loop and not kept_tool_loop:
        raise ValueError("Context budget cannot fit the latest intact model/tool exchange")
    kept_history = keep_recent(group_tool_exchanges(history))
    return [
        *system_messages,
        *(message for group in kept_history for message in group),
        round_message,
        *(message for group in kept_tool_loop for message in group),
    ]


def _resolve_auto_tool_rounds(agent: AgentContext, max_tool_rounds: int | None = None) -> int:
    """Resolve the internal follow-up cap for one model-led turn."""

    if isinstance(max_tool_rounds, int) and max_tool_rounds > 0:
        return max_tool_rounds
    session = getattr(getattr(agent, "config", None), "session", None)
    configured = getattr(session, "solve_max_tool_rounds", None)
    if isinstance(configured, int) and configured > 0:
        return configured
    return _DEFAULT_AUTO_TOOL_ROUNDS








def _append_context_message(agent: AgentContext, message: dict[str, Any]) -> None:
    context = getattr(agent, "context", None)
    if context is None:
        return
    add_message = getattr(context, "add_message", None)
    if callable(add_message):
        add_message(message)
        return
    role = message.get("role")
    content = str(message.get("content", "") or "")
    if role == "assistant" and content and callable(getattr(context, "add_assistant_message", None)):
        context.add_assistant_message(content)
    elif role == "user" and content and callable(getattr(context, "add_user_message", None)):
        context.add_user_message(content)


def extract_response(message: Any) -> str:
    """Extract the actual response text from an LLM message.

    Handles:
    1. Normal content (no thinking)
    2. Content with inline <thinking> tags (open/closed)
    3. Separate reasoning_content field (DeepSeek R1, etc.)
    """
    content = message.content or ""
    reasoning = getattr(message, "reasoning_content", None) or ""
    if reasoning and not content:
        content = f"<thinking>\n{reasoning}\n</thinking>\n"
    elif reasoning and content:
        content = f"<thinking>\n{reasoning}\n</thinking>\n{content}"
    return content


def _is_non_retriable_llm_error(error_text: str) -> bool:
    """Return True for configuration/auth errors that should fail fast."""
    hard_fail_markers = [
        "bad_request_error",
        "incorrect api key",
        "invalid api key",
        "invalid chat setting",
        "invalid function arguments json string",
        "tool_call_id",
        "authentication",
        "unauthorized",
        "permission denied",
        "model not found",
        "no such model",
        "invalid_request_error",
        "unsupported parameter",
    ]
    return any(marker in error_text for marker in hard_fail_markers)


def _is_key_exhausted_error(error_text: str) -> bool:
    """Return True for errors that mean the *current* API key is unusable.

    These are rate-limit / quota / balance exhaustion signals where switching to
    a different key is the right recovery. Covers OpenAI-style 429/quota plus
    deepseek (402 insufficient balance) and zhipu (codes 1302/1113, 余额) errors.
    """
    exhausted_markers = [
        "rate limit",
        "rate_limit",
        "too many requests",
        "429",
        "quota",
        "insufficient balance",
        "余额",  # zhipu/deepseek: account balance insufficient
        "402",
        "1302",  # zhipu: concurrency / rate limit
        "1113",  # zhipu: account balance insufficient
    ]
    return any(marker in error_text for marker in exhausted_markers)


from vulnclaw.i18n import _  # noqa: E402


def build_llm_request(
    agent: AgentContext, messages: list[dict[str, Any]], tools: list[dict] | None = None,
    *, max_tokens: Any = UNSET, temperature: Any = UNSET,
) -> dict[str, Any]:
    configured = agent.config.llm.max_tokens if max_tokens is UNSET else max_tokens
    bounded = _prepare_subagent_llm_request(agent, messages, configured)
    diagnostics: list = []
    kwargs = build_request(
        agent.config.llm, messages, tools, max_tokens=bounded if _is_subagent(agent) else configured,
        temperature=temperature, diagnostics=diagnostics,
    )
    agent._llm_diagnostics = diagnostics
    return kwargs


async def _call_with_persistent_retries_unbudgeted(
    agent: AgentContext, request_fn, stage_label: str, max_retries: int = 20,
    *, accept_response=None,
) -> tuple[Any, int]:
    """Keep retrying retriable LLM calls until success, max retries, or manual interruption.

    Args:
        max_retries: Maximum number of retry attempts before raising RuntimeError.
                     Default is 20 (at 5s intervals = ~100s total wait).

    Returns:
        (response, retry_attempts)

    Raises:
        RuntimeError: If max_retries is exceeded.
    """
    loop = asyncio.get_running_loop()
    retry_attempts = 0
    pool_size = len(getattr(agent, "_key_pool", None) or [])
    can_rotate = pool_size > 1 and callable(getattr(agent, "rotate_api_key", None))
    keys_tried: set[int] = set()

    while retry_attempts < max_retries:
        try:
            maybe_response = loop.run_in_executor(None, request_fn)
            if inspect.isawaitable(maybe_response):
                response = await await_stream_open(maybe_response) if accept_response else await maybe_response
            else:
                response = maybe_response
            if accept_response is not None and accept_response(response):
                return response, retry_attempts
            if isinstance(response, Result):
                return response.require_complete(), retry_attempts
            if response is not None and getattr(response, "choices", None):
                return response, retry_attempts

            retry_attempts += 1
            logger.warning(
                "%s LLM API 异常响应，第 %d 次重连尝试中... (5s 后重试)",
                stage_label, retry_attempts,
            )
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            if isinstance(exc, (ValueError, TypeError, ImportError, AttributeError)):
                raise
            status = getattr(exc, "status_code", None)
            if status in {400, 403, 404, 405, 422}:
                raise
            error_text = str(exc).lower()
            is_exhausted = _is_key_exhausted_error(error_text)
            is_auth = status == 401 or any(term in error_text for term in (
                "incorrect api key", "invalid api key", "authentication", "unauthorized",
            ))
            is_depleted = status == 402 or any(term in error_text for term in (
                "insufficient_quota", "insufficient balance", "1113", "余额不足",
            ))
            if _is_non_retriable_llm_error(error_text) and not is_auth and not is_exhausted:
                raise

            # Multi-key failover: rotate past a rate-limited / quota-drained /
            # invalid key to the next one before falling back to plain retry.
            if can_rotate and (is_exhausted or is_auth):
                keys_tried.add(getattr(agent, "_key_index", 0))
                if len(keys_tried) < pool_size:
                    agent.rotate_api_key()
                    retry_attempts += 1
                    logger.warning(
                        "%s 当前密钥失败 (%s)，切换到下一个 API 密钥并重试...",
                        stage_label, exc,
                    )
                    continue
                # Every key has now failed in this burst.
                if (is_auth and not is_exhausted) or is_depleted:
                    # All keys are invalid/unauthorized -> nothing to recover.
                    raise
                # All keys rate-limited: keep cycling, but back off first so we
                # never hard-fail on transient quota limits.
                keys_tried.clear()
                agent.rotate_api_key()
                retry_attempts += 1
                logger.warning(
                    "%s 所有 API 密钥均已限流，第 %d 次重连尝试中... (5s 后重试)",
                    stage_label, retry_attempts,
                )
                await asyncio.sleep(5)
                continue

            if (is_auth and not is_exhausted) or is_depleted:
                raise

            from openai import APIConnectionError

            transient = (
                status in {408, 409, 429} or isinstance(status, int) and status >= 500
                or isinstance(exc, (ConnectionError, TimeoutError, APIConnectionError))
                or is_exhausted
                or any(term in error_text for term in ("connection", "timeout", "timed out"))
            )
            if not transient:
                raise

            retry_attempts += 1
            logger.warning(
                "%s LLM 连接异常，第 %d 次重连尝试中... (%s)",
                stage_label, retry_attempts, exc,
            )
            await asyncio.sleep(5)

    raise RuntimeError(_("agent.llm.max_retries", stage=stage_label, retries=max_retries))


async def _call_with_persistent_retries(
    agent: AgentContext, request_fn, stage_label: str, max_retries: int = 20
) -> tuple[Any, int]:
    admission = _reserve_subagent_llm_request(agent)
    try:
        response, retries = await _call_with_persistent_retries_unbudgeted(
            agent, request_fn, stage_label, max_retries
        )
        actual_tokens = _record_subagent_llm_usage(agent, response)
        _settle_subagent_llm_admission(agent, admission, actual_tokens)
        admission = None
        return response, retries
    except ProtocolError as exc:
        partial = getattr(exc, "result", None)
        if partial is not None and partial.usage is not None:
            actual_tokens = _record_subagent_llm_usage(agent, partial)
            _settle_subagent_llm_admission(agent, admission, actual_tokens)
            admission = None
        raise
    finally:
        if admission is not None:
            _fail_subagent_llm_admission(agent, admission)


def _prepend_retry_notice(text: str, retry_attempts: int) -> str:
    """Annotate a successful response if retries happened within the same round."""
    if retry_attempts <= 0:
        return text
    return _("agent.llm.recovered", attempts=retry_attempts) + "\n" + text


def _format_tool_results_fallback(
    tool_results: list[dict[str, Any]],
    skipped_info: list[str],
    *,
    assistant_text: str = "",
) -> str:
    """Build deterministic tool-result text from the model-facing observations."""

    parts = [
        "[tool results processed] 工具调用已执行；未进行额外 LLM 总结；已降级为纯文本结果摘要。"
    ]
    if assistant_text.strip():
        parts.append(f"模型行动理由: {assistant_text.strip()[:600]}")
    for item in tool_results:
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        content = str(item.get("content", ""))
        duration_ms = item.get("duration_ms")
        correction = str(item.get("correction") or "").strip()
        prefix = ""
        tool_call = item.get("tool_call")
        tool_name = getattr(getattr(tool_call, "function", None), "name", "")
        if tool_name:
            prefix = f"工具 {tool_name}"
            if isinstance(duration_ms, int):
                prefix += f" ({duration_ms}ms)"
            prefix += ": "
        parts.append(prefix + content)
        if correction:
            parts.append(f"纠偏信号: {correction}")
    if skipped_info:
        parts.append("本轮提示: " + "; ".join(skipped_info))
    return "\n".join(parts)


def _append_context_items(agent: AgentContext, items: list[dict]) -> None:
    context = getattr(agent, "context", None)
    add = getattr(context, "add_messages", None)
    if callable(add):
        add(items)
    else:
        for item in items:
            _append_context_message(agent, item)


async def _generate(agent: AgentContext, messages: list[dict], tools: list[dict],
                    sink: Any = None) -> Result:
    kwargs = build_llm_request(agent, messages, tools)
    if sink is None:
        result, retries = await _call_with_persistent_retries(
            agent, lambda: create(agent._get_client(), agent.config.llm, kwargs), "LLM",
        )
        result.retry_attempts = retries
        return result
    sink.on_status("Thinking...")
    seen = getattr(agent, "_llm_seen_diagnostics", set())
    agent._llm_seen_diagnostics = seen
    for diagnostic in getattr(agent, "_llm_diagnostics", []):
        key = (diagnostic.parameter, str(diagnostic.requested), str(diagnostic.effective), diagnostic.reason)
        if key in seen:
            continue
        seen.add(key)
        sink.on_status(f"{diagnostic.parameter}: {diagnostic.requested!r} → "
                       f"{diagnostic.effective!r} ({diagnostic.reason})")
    admission = _reserve_subagent_llm_request(agent)
    try:
        from vulnclaw.llm.client import start_stream

        retries = 0

        async def open_stream():
            nonlocal retries
            response, retries = await _call_with_persistent_retries_unbudgeted(
                agent, lambda: start_stream(agent._get_client(), agent.config.llm, kwargs), "LLM stream",
                accept_response=lambda value: value is not None,
            )
            return response

        result = await stream_result(None, agent.config.llm, kwargs, sink, open_stream=open_stream)
        result.retry_attempts = retries
        used = _record_subagent_llm_usage(agent, result)
        _settle_subagent_llm_admission(agent, admission, used)
        admission = None
        return result.require_complete()
    except ProtocolError as exc:
        partial = getattr(exc, "result", None)
        if admission is not None and partial is not None and partial.usage is not None:
            used = _record_subagent_llm_usage(agent, partial)
            _settle_subagent_llm_admission(agent, admission, used)
            admission = None
        raise
    finally:
        if admission is not None:
            _fail_subagent_llm_admission(agent, admission)


async def _run_loop(agent: AgentContext, messages: list[dict], *, include_history: bool,
                    max_tool_rounds: int | None, sink: Any = None) -> str:
    tools = agent._build_openai_tools()
    last_results = None
    retries = 0
    current = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    for round_index in range(_resolve_auto_tool_rounds(agent, max_tool_rounds) + 1):
        if current is not None:
            messages = _fit_tool_loop_context(agent, messages, current)
        messages = _fit_context_window(agent, messages, tools, purpose="tool_follow_up")
        try:
            result = await _generate(agent, messages, tools, sink)
            retries += result.retry_attempts
        except (Exception, asyncio.CancelledError) as exc:
            partial = getattr(exc, "result", None)
            state = getattr(getattr(agent, "context", None), "state", None)
            if isinstance(partial, Result) and state is not None:
                state.llm_interruption = {
                    "response_id": partial.response_id, "status": partial.status,
                    "content": partial.content, "reason": partial.incomplete_reason,
                    "usage": partial.usage.raw if partial.usage else None,
                }
            if last_results is not None and not isinstance(exc, (ProtocolError, asyncio.CancelledError)):
                return _format_tool_results_fallback(last_results, []) + f"\n[llm follow-up failed] {exc}"
            raise
        if not result.tool_calls:
            if include_history:
                _append_context_items(agent, result.output_items)
            return _prepend_retry_notice(extract_response(result), retries)
        if sink:
            for call in result.tool_calls:
                sink.on_tool_call(call.function.name, call.function.arguments[:200])
        try:
            tool_results, skipped = await handle_tool_calls_with_results(agent, result)
        except asyncio.CancelledError:
            cancelled = [{"type": "function_call_output", "call_id": c.id,
                          "output": getattr(agent, "_llm_tool_results", {}).get(c.id, {}).get("content",
                              "Cancelled; execution outcome may be unknown. Do not replay automatically.")}
                         for c in result.tool_calls]
            if include_history:
                _append_context_items(agent, [*result.output_items, *cancelled])
            raise
        last_results = tool_results
        outputs = [{"type": "function_call_output", "call_id": item["tool_call_id"],
                    "output": str(item.get("content", ""))} for item in tool_results]
        exchange = [*result.output_items, *outputs]
        messages.extend(exchange)
        if include_history:
            _append_context_items(agent, exchange)
        if sink:
            for item in tool_results:
                sink.on_tool_result(str(item.get("content", "")))
        if round_index == _resolve_auto_tool_rounds(agent, max_tool_rounds):
            return _format_tool_results_fallback(tool_results, skipped)
    raise AssertionError("unreachable")


async def call_llm(agent: AgentContext, system_prompt: str, *, stream_sink: Any = None) -> str:
    messages = [{"role": "system", "content": system_prompt}, *agent.context.get_messages()]
    return await _run_loop(agent, messages, include_history=True, max_tool_rounds=None, sink=stream_sink)


async def call_llm_auto(agent: AgentContext, system_prompt: str, round_context: str, *,
                        stream_sink: Any = None, include_history: bool = True,
                        max_tool_rounds: int | None = None) -> str:
    messages, current = _build_tool_loop_messages(
        agent, system_prompt, round_context, include_history=include_history,
    )
    if include_history:
        _append_context_message(agent, current)
    return await _run_loop(agent, messages, include_history=include_history,
                           max_tool_rounds=max_tool_rounds, sink=stream_sink)


async def call_llm_stream(agent: AgentContext, system_prompt: str, stream_sink: Any = None) -> str:
    return await call_llm(agent, system_prompt, stream_sink=stream_sink or _NullSink())


async def call_llm_auto_stream(agent: AgentContext, system_prompt: str, round_context: str,
                               stream_sink: Any = None, *, include_history: bool = True,
                               max_tool_rounds: int | None = None) -> str:
    return await call_llm_auto(agent, system_prompt, round_context,
                               stream_sink=stream_sink or _NullSink(),
                               include_history=include_history, max_tool_rounds=max_tool_rounds)


# === Stream Output Protocol ===


@runtime_checkable
class StreamSink(Protocol):
    """输出流接收器抽象。

    LLM 调用层通过此接口将输出定向到不同目标（CLI/Web/静默）。
    放在 llm_client.py 中符合 CONTRIBUTING.md 的模块放置原则。
    """

    def on_status(self, message: str) -> None:
        """显示状态提示（如 "Thinking..."）。"""
        ...

    def on_thinking_token(self, token: str) -> None:
        """接收思考过程的 token（可选择是否显示）。"""
        ...

    def on_content_token(self, token: str) -> None:
        """接收正文 token。"""
        ...

    def on_tool_call(self, tool_name: str, args: str) -> None:
        """显示工具调用提示。"""
        ...

    def on_tool_result(self, result_summary: str) -> None:
        """显示工具结果摘要。"""
        ...

    def on_stream_end(self) -> None:
        """流式结束回调（换行/清理）。"""
        ...


class _NullSink:
    """空实现，确保无 sink 时不产生任何输出。"""

    def on_status(self, message: str) -> None:
        pass

    def on_thinking_token(self, token: str) -> None:
        pass

    def on_content_token(self, token: str) -> None:
        pass

    def on_tool_call(self, tool_name: str, args: str) -> None:
        pass

    def on_tool_result(self, result_summary: str) -> None:
        pass

    def on_stream_end(self) -> None:
        pass
