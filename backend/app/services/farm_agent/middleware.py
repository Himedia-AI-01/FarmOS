"""Custom deepagents middleware: tool-result clearing + token budget guards.

Data preservation note (May 2026 review):
    ToolResultClearingMiddleware previously discarded elided ToolMessages
    in-process. That destroyed exactly the data the recorded-trace replay
    harness needs (see app.services.farm_agent.replay). We now mirror every
    elided message to the Redis stream ``traces:tools:elided`` BEFORE
    rewriting the messages list. The mirror is best-effort — if Redis is
    down, we still elide (otherwise context overflow returns) but log a
    warning so the gap is visible. The stream is auto-trimmed to 100k
    entries with MAXLEN ~.



Two harness-engineering primitives that the May 2026 Anthropic post called out
as essential for long-running agents (see "Harness design for long-running
apps"). We implement them provider-agnostically because we run on Grok via
OpenRouter, which has no native ``clear_tool_uses_20250919`` header.

ToolResultClearingMiddleware
    Once the conversation grows beyond a threshold of tool-result blocks,
    elide all but the most recent N. Older ``ToolMessage``s become a single
    placeholder summary so the model still knows tools fired but doesn't
    re-read every chunk of regulatory text on every turn. This is the most
    impactful single fix for the subsidy agent — chunked PDF fetches
    accumulate fast and bloat each subsequent LLM call.

SubAgentTokenBudgetMiddleware
    Pre-flight estimate of input tokens; abort the model call with a
    deterministic error if a sub-agent's request exceeds its budget. The
    deepagents v0.5 sub-agent loop has no native budget guard — without
    this, a runaway BM25→rerank loop in the subsidy sub-agent can burn
    well over $0.50 in a single turn (see aicosts.ai postmortem on the
    subagent cost explosion).

Both are best-effort and never raise out. Failures degrade to "leave
the request alone" because partial protection is better than a hard
crash mid-stream.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

logger = logging.getLogger(__name__)


# ── Token-counting helper ───────────────────────────────────────────────────
#
# We use a cheap char-based approximation rather than calling tiktoken /
# anthropic.tokenizer because:
#   1. We're token-aware, not token-precise — both middlewares set high
#      thresholds that don't need ±1 accuracy.
#   2. Loading tiktoken adds ~80MB of cold-start memory and ~200ms of
#      import time; our budgets only need to catch order-of-magnitude
#      runaways, not precise truncation.
#   3. Korean text (한자/한글) tokenises differently per provider; a
#      char-count is a stable lower bound across providers.
#
# Empirical Korean+English mix on Grok and Claude: ~3.5 chars/token.
# We use 3.0 to round conservatively — overestimate tokens, trip the
# guard slightly early. Better than late.
_CHARS_PER_TOKEN = 3.0


def _approx_tokens(text: str) -> int:
    if not text:
        return 0
    return int(len(text) / _CHARS_PER_TOKEN) + 1


def _message_text(msg: BaseMessage) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                t = item.get("text") or item.get("content") or ""
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)
    return str(content)


def _approx_message_tokens(msg: BaseMessage) -> int:
    return _approx_tokens(_message_text(msg))


def _approx_request_tokens(messages: list[AnyMessage], system_text: str = "") -> int:
    total = _approx_tokens(system_text)
    for m in messages:
        total += _approx_message_tokens(m)
    # Per-message overhead (role tokens, separators) — Anthropic & OpenAI
    # both add ~4-8 tokens per message envelope.
    total += 6 * len(messages)
    return total


# ── Base class adapter (handles deepagents/langchain version drift) ────────


def _make_middleware_base() -> Any:
    """Resolve ``AgentMiddleware`` across deepagents/langchain versions.

    We don't import this at module top-level because the import path moved
    once between deepagents 0.4 and 0.5; doing this lazily lets us emit a
    helpful warning if the install is older.
    """
    try:
        from langchain.agents.middleware.types import AgentMiddleware  # type: ignore[attr-defined]

        return AgentMiddleware
    except ImportError:
        try:
            from deepagents.middleware.types import AgentMiddleware  # type: ignore[attr-defined]

            return AgentMiddleware
        except ImportError as exc:
            logger.warning(
                "middleware.import_failed AgentMiddleware not found — "
                "tool-clearing and token-budget guards will be disabled. err=%s",
                exc,
            )
            return None


_AgentMiddleware = _make_middleware_base()


# ── ToolResultClearingMiddleware ────────────────────────────────────────────


class ToolResultClearingMiddleware(_AgentMiddleware if _AgentMiddleware else object):  # type: ignore[misc]
    """Evict stale ``ToolMessage``s once a recency window is full.

    Anthropic's ``clear_tool_uses_20250919`` does this server-side; we
    do it client-side for provider portability. Pairs the most recent
    ``keep_last_n`` ToolMessages (and their corresponding tool-call
    AIMessages) and replaces older ones with a single placeholder summary.

    The guard only kicks in when:
      - the message list has at least ``min_messages_before_clear``
        entries, AND
      - the count of ``ToolMessage`` entries exceeds ``keep_last_n``.

    System messages, the most recent HumanMessage, and the final
    AIMessage are never elided.
    """

    name = "ToolResultClearingMiddleware"

    def __init__(
        self,
        *,
        keep_last_n: int = 3,
        min_messages_before_clear: int = 12,
        placeholder_template: str = (
            "[이전 도구 호출 {n}건은 길이 절약을 위해 요약되었습니다 "
            "— 결과는 이미 답변에 반영됨]"
        ),
        archive_stream: str | None = "traces:tools:elided",
        archive_maxlen: int = 100_000,
    ) -> None:
        super().__init__()
        self.keep_last_n = max(1, int(keep_last_n))
        self.min_messages_before_clear = max(1, int(min_messages_before_clear))
        self.placeholder_template = placeholder_template
        # Archive stream — set to None to disable mirroring (tests). When
        # set, every elided message is XADDed before being dropped from
        # the live request, so replay tests can reconstruct the full turn.
        self.archive_stream = archive_stream
        self.archive_maxlen = max(1000, int(archive_maxlen))
        # Strong-reference set for fire-and-forget archive tasks. asyncio only
        # holds a weak reference to a Task once it's scheduled; if our caller
        # discards the create_task() return value, GC can collect the Task
        # mid-run and the archive silently disappears.
        self._pending_archive_tasks: set[Any] = set()

    # The MemoryMiddleware shows that overriding wrap_model_call /
    # awrap_model_call is the right hook for read-modify-forward patterns.
    async def _archive(self, elided: list[AnyMessage]) -> None:
        """Best-effort mirror of dropped messages to a Redis stream.

        We swallow every exception so an archive failure can't undo the
        elision (and let the agent's context overflow). Replay tooling
        treats missing entries as "before the archive existed."
        """
        if not self.archive_stream or not elided:
            return
        try:
            from app.core.redis import get_redis  # local import to avoid cycle at boot
        except Exception:  # noqa: BLE001
            return
        client = get_redis()
        if client is None:
            logger.warning(
                "tool_result_clearing.archive_skip reason=redis_disabled n=%d "
                "— replay harness will see a gap",
                len(elided),
            )
            return
        try:
            import json as _json
            import time as _time

            ts = int(_time.time() * 1000)
            pipe = client.pipeline(transaction=False)
            for i, m in enumerate(elided):
                payload = {
                    "ts": str(ts),
                    "seq": str(i),
                    "type": type(m).__name__,
                    "tool_call_id": str(getattr(m, "tool_call_id", "") or ""),
                    "content": _json.dumps(
                        _message_text(m) if not isinstance(m, AIMessage)
                        else {"text": _message_text(m), "tool_calls": getattr(m, "tool_calls", None)},
                        ensure_ascii=False,
                        default=str,
                    )[:8000],
                }
                pipe.xadd(
                    self.archive_stream,
                    payload,
                    maxlen=self.archive_maxlen,
                    approximate=True,
                )
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "tool_result_clearing.archive_failed n=%d err=%s",
                len(elided), exc,
            )

    def _plan_clear(
        self, messages: list[AnyMessage]
    ) -> tuple[list[AnyMessage], list[AnyMessage]]:
        """Plan an elision. Returns (new_messages, elided_messages).

        If no elision is warranted, returns (messages, []) — caller checks
        ``elided`` to decide whether to archive.
        """
        if len(messages) < self.min_messages_before_clear:
            return messages, []

        # Walk in reverse, count ToolMessages, find the cut point.
        tool_count = 0
        cut_idx: int | None = None
        for i in range(len(messages) - 1, -1, -1):
            m = messages[i]
            if isinstance(m, ToolMessage):
                tool_count += 1
                if tool_count > self.keep_last_n:
                    cut_idx = i
                    break
        if cut_idx is None:
            return messages, []

        # Count how many ToolMessages we're eliding for the placeholder text.
        elided_tool_count = sum(
            1 for m in messages[: cut_idx + 1] if isinstance(m, ToolMessage)
        )
        if elided_tool_count == 0:
            return messages, []

        # Keep: all SystemMessages and HumanMessages from the elided range
        # (they carry user intent / instructions); drop ToolMessages and
        # tool-call AIMessages entirely. Replace the dropped run with one
        # synthetic ToolMessage placeholder so the conversation still has
        # a tool-result anchor for the model. Track everything we drop so
        # the archive stream gets the full record.
        kept: list[AnyMessage] = []
        elided: list[AnyMessage] = []
        for m in messages[: cut_idx + 1]:
            if isinstance(m, (SystemMessage, HumanMessage)):
                kept.append(m)
            elif isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
                # AI messages WITHOUT tool calls are normal answers — keep.
                kept.append(m)
            else:
                # Drop: ToolMessage, AIMessage with tool_calls
                elided.append(m)

        placeholder = ToolMessage(
            content=self.placeholder_template.format(n=elided_tool_count),
            tool_call_id="__elided__",
        )
        kept.append(placeholder)
        kept.extend(messages[cut_idx + 1 :])
        logger.info(
            "tool_result_clearing.applied elided_tool=%d elided_total=%d "
            "kept_n=%d total_before=%d total_after=%d",
            elided_tool_count, len(elided), self.keep_last_n,
            len(messages), len(kept),
        )
        return kept, elided

    def _sync_modify(self, request: Any) -> tuple[Any, list[AnyMessage]]:
        try:
            new_messages, elided = self._plan_clear(request.messages)
            if not elided:
                return request, []
            return request.override(messages=new_messages), elided
        except Exception as exc:  # noqa: BLE001 — never break the agent loop
            logger.warning("tool_result_clearing.modify_failed err=%s", exc)
            return request, []

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        new_request, elided = self._sync_modify(request)
        if elided and self.archive_stream:
            # Sync path can't await — schedule the archive on the running
            # loop if there is one; otherwise drop the archive (this code
            # path is rare in deepagents — almost all calls go through
            # awrap_model_call).
            try:
                import asyncio

                loop = asyncio.get_running_loop()
                task = loop.create_task(self._archive(elided))
                self._pending_archive_tasks.add(task)
                task.add_done_callback(self._pending_archive_tasks.discard)
            except RuntimeError:
                logger.warning(
                    "tool_result_clearing.sync_no_loop n=%d — archive skipped",
                    len(elided),
                )
        return handler(new_request)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        new_request, elided = self._sync_modify(request)
        if elided:
            await self._archive(elided)
        return await handler(new_request)


# ── SubAgentTokenBudgetMiddleware ───────────────────────────────────────────


class TokenBudgetExceeded(Exception):
    """Raised inside the sub-agent loop when the budget is breached.

    The middleware *does not* raise — it returns a synthetic answer
    instead — but this class exists so callers and tests can pattern-match
    on the failure mode.
    """


class SubAgentTokenBudgetMiddleware(_AgentMiddleware if _AgentMiddleware else object):  # type: ignore[misc]
    """Pre-flight token guard for sub-agent runs.

    Estimates the request size; if it exceeds ``max_input_tokens`` we
    short-circuit by replacing the messages with a synthetic abort
    message so the sub-agent returns a graceful "budget exceeded" answer
    instead of running and overshooting cost.

    Why a synthetic answer (not a raise): exceptions inside the deepagents
    sub-agent loop bubble as ``GraphRecursionError`` or ``CancelledError``
    with no clear message, which is exactly what we just fixed for the
    recursion-limit bug. A graceful synthesised answer keeps the parent
    orchestrator's flow intact and gives the user a clear cause.
    """

    name = "SubAgentTokenBudgetMiddleware"

    def __init__(
        self,
        *,
        agent_name: str,
        max_input_tokens: int,
        soft_warn_ratio: float = 0.8,
    ) -> None:
        super().__init__()
        self.agent_name = agent_name
        self.max_input_tokens = max(1024, int(max_input_tokens))
        self.soft_warn_ratio = max(0.0, min(1.0, float(soft_warn_ratio)))

    def _evaluate(self, request: Any) -> Any:
        try:
            sys_text = ""
            sm = getattr(request, "system_message", None)
            if sm is not None:
                sys_text = _message_text(sm)
            estimate = _approx_request_tokens(request.messages, sys_text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("token_budget.estimate_failed agent=%s err=%s", self.agent_name, exc)
            return request

        if estimate < int(self.max_input_tokens * self.soft_warn_ratio):
            return request

        if estimate < self.max_input_tokens:
            logger.warning(
                "token_budget.soft_warn agent=%s estimate=%d max=%d ratio=%.2f",
                self.agent_name, estimate, self.max_input_tokens,
                estimate / self.max_input_tokens,
            )
            return request

        # Hard breach — short-circuit. Return a request whose messages
        # carry a single instruction asking the model to apologise + halt.
        logger.warning(
            "token_budget.exceeded agent=%s estimate=%d max=%d — short-circuiting",
            self.agent_name, estimate, self.max_input_tokens,
        )
        abort_text = (
            f"[budget exceeded] The {self.agent_name} sub-agent's input "
            f"({estimate} approx tokens) exceeds its budget "
            f"({self.max_input_tokens}). Reply in Korean with a one-sentence "
            "apology that the request was too large to process and suggest "
            "the user split it. Do not call any tools."
        )
        try:
            return request.override(messages=[HumanMessage(content=abort_text)])
        except Exception:  # noqa: BLE001
            # Override unsupported on this version — return as-is and let
            # the run proceed (degraded but functional).
            return request

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._evaluate(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._evaluate(request))


# ── Factories used by agent.py ─────────────────────────────────────────────


def make_tool_result_clearing_middleware(
    *,
    keep_last_n: int,
    min_messages_before_clear: int,
) -> Any | None:
    """Returns an instance, or None if the base class is unavailable."""
    if _AgentMiddleware is None:
        return None
    return ToolResultClearingMiddleware(
        keep_last_n=keep_last_n,
        min_messages_before_clear=min_messages_before_clear,
    )


def make_subagent_token_budget(
    *,
    agent_name: str,
    max_input_tokens: int,
) -> Any | None:
    if _AgentMiddleware is None:
        return None
    return SubAgentTokenBudgetMiddleware(
        agent_name=agent_name,
        max_input_tokens=max_input_tokens,
    )
