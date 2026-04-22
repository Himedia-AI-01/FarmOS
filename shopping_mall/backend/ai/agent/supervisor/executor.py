"""SupervisorExecutor — tool_use 루프로 서브 에이전트를 오케스트레이션합니다."""
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.database import SessionLocal

from ai.agent.clients.base import AgentClient, AgentUnavailableError, ToolCall
from ai.agent.executor import (
    AgentExecutor,
    AgentResult,
    RequestContext,
    TraceStep,
    ToolMetricData,
    _parse_answer,
    _log_trace,
    MAX_ITERATIONS,
)
from .tools import SUPERVISOR_TOOLS

logger = logging.getLogger(__name__)


# 취소 플로우 감지 키워드
_CANCEL_KEYWORDS_ACTION: frozenset[str] = frozenset({"취소", "cancel", "환불"})
_EXCHANGE_KEYWORDS_ACTION: frozenset[str] = frozenset({
    "교환", "exchange", "반품", "교체",
    # 상품 불량·하자 — 명시적 교환/반품 언급 없어도 교환 플로우로 유추
    "벌레", "이물질", "불량", "상함", "파손", "하자", "오배송",
    "썩음", "곰팡이", "망가", "깨짐", "냄새", "이상해", "상했",
})

# Supervisor에서 CS 도구는 병렬 실행 가능 (읽기 전용)
# Order 도구는 LangGraph 상태 + DB 쓰기 포함 → 순차 실행
_PARALLEL_SUPERVISOR_TOOLS: frozenset[str] = frozenset({"call_cs_agent"})

_SUPERVISOR_TOOL_TO_INTENT: dict[str, str] = {
    "call_cs_agent": "other",
    # call_order_agent은 action("cancel"|"exchange")이 동적이므로 여기에 포함하지 않음.
    # 각 callsite에서 _detect_order_action(user_message)로 결정한다.
}


@dataclass
class _OrderPendingResult:
    """OrderGraph 중단 상태 — Supervisor LLM 재합성 없이 직접 전달."""
    message: str


def _detect_order_action(query: str) -> str:
    """쿼리에서 교환/취소 의도 감지. 기본값: 'cancel'."""
    q = query.lower()
    exchange_score = sum(1 for kw in _EXCHANGE_KEYWORDS_ACTION if kw in q)
    cancel_score = sum(1 for kw in _CANCEL_KEYWORDS_ACTION if kw in q)
    return "exchange" if exchange_score > cancel_score else "cancel"


class SupervisorExecutor:
    """Supervisor tool_use 루프.

    보유 도구:
    - call_cs_agent  → CSAgentExecutor (기존 AgentExecutor 재사용)
    - call_order_agent → OrderGraph (LangGraph StateGraph)
    """

    def __init__(
        self,
        primary: AgentClient,
        fallback: AgentClient | None,
        cs_executor: AgentExecutor,
        cs_input_prompt: str,
        cs_output_prompt: str,
        order_graph,  # LangGraph CompiledStateGraph
        max_iterations: int = 5,  # Supervisor는 서브 에이전트 호출만 — 반복 적음
    ):
        self.primary = primary
        self.fallback = fallback
        self.cs_executor = cs_executor
        self.cs_input_prompt = cs_input_prompt
        self.cs_output_prompt = cs_output_prompt
        self.order_graph = order_graph
        self.max_iterations = max_iterations
        self.tools = SUPERVISOR_TOOLS

    # ── 진입점 ────────────────────────────────────────────────────────────

    async def run(
        self,
        db: Session,
        user_message: str,
        user_id: int | None,
        history: list[dict],
        input_system: str,
        output_system: str,
        session_id: int | None = None,
        context: RequestContext | None = None,
    ) -> AgentResult:
        """Supervisor 루프 실행. Primary 실패 시 Fallback으로 전환."""
        ctx = context or RequestContext.build(user_id)
        suffix = ctx.to_system_suffix()
        input_with_ctx = input_system + suffix
        output_with_ctx = output_system + suffix

        try:
            return await self._run_loop(
                self.primary, db, user_message, user_id, session_id, history,
                input_with_ctx, output_with_ctx,
            )
        except AgentUnavailableError as e:
            logger.warning(f"[supervisor] Primary LLM 실패: {e}. Fallback 시도.")
            if self.fallback:
                try:
                    return await self._run_loop(
                        self.fallback, db, user_message, user_id, session_id, history,
                        input_with_ctx, output_with_ctx,
                    )
                except AgentUnavailableError as e2:
                    logger.error(f"[supervisor] Fallback LLM도 실패: {e2}")
            return AgentResult(
                answer="죄송합니다. 현재 서비스에 일시적인 문제가 발생했습니다. 잠시 후 다시 시도하거나 고객센터(1588-0000)로 문의해 주세요.",
                intent="escalation",
                escalated=True,
            )

    # ── 루프 ──────────────────────────────────────────────────────────────

    async def _run_loop(
        self,
        client: AgentClient,
        db: Session,
        user_message: str,
        user_id: int | None,
        session_id: int | None,
        history: list[dict],
        input_system: str,
        output_system: str,
    ) -> AgentResult:
        messages = list(history) + [{"role": "user", "content": user_message}]
        tools_used: list[str] = []
        trace: list[TraceStep] = []
        metrics: list[ToolMetricData] = []
        escalated = False

        # OrderGraph 진행 중인 플로우가 있으면 바로 OrderGraph로 넘긴다
        # (Supervisor LLM 판단 없이 직접 재개)
        if session_id and user_id and await self._has_pending_order_flow(session_id):
            response_text = await self._call_order_agent(
                user_message, user_id, session_id, db
            )
            intent = _detect_order_action(user_message)
            _log_trace(trace, user_message)
            return AgentResult(
                answer=response_text,
                intent=intent,
                escalated=False,
                tools_used=["call_order_agent"],
                trace=trace,
                metrics=metrics,
            )

        for iteration in range(self.max_iterations):
            response = await client.chat_with_tools(messages, self.tools, input_system)

            # 도구 호출 없음 → 최종 답변 생성
            if not response.tool_calls:
                if tools_used:
                    # 서브 에이전트 결과가 있으면 output_system으로 응답 합성
                    synth = await client.chat_with_tools(messages, [], output_system)
                    raw_answer = synth.text or "죄송합니다. 답변을 생성하지 못했습니다."
                else:
                    # 서브 에이전트 없이 직접 답변 (엣지 케이스)
                    raw_answer = response.text or "죄송합니다. 답변을 생성하지 못했습니다."
                answer = _parse_answer(raw_answer)
                _first = tools_used[0] if tools_used else None
                intent = (
                    _detect_order_action(user_message)
                    if _first == "call_order_agent"
                    else _SUPERVISOR_TOOL_TO_INTENT.get(_first, "other") if _first else "other"
                )
                _log_trace(trace, user_message)
                return AgentResult(
                    answer=answer,
                    intent=intent,
                    escalated=escalated,
                    tools_used=tools_used,
                    trace=trace,
                    metrics=metrics,
                )

            # 메타데이터 수집
            for tc in response.tool_calls:
                tools_used.append(tc.name)

            # ── 도구 실행: CS는 병렬, Order는 순차 ────────────────────────
            parallel_indexed = [
                (i, tc) for i, tc in enumerate(response.tool_calls)
                if tc.name in _PARALLEL_SUPERVISOR_TOOLS
            ]
            sequential_indexed = [
                (i, tc) for i, tc in enumerate(response.tool_calls)
                if tc.name not in _PARALLEL_SUPERVISOR_TOOLS
            ]

            timed_results: list = [None] * len(response.tool_calls)
            pending_result: _OrderPendingResult | None = None

            # 병렬 실행 (CS 에이전트) — 각 호출에 독립 Session 할당
            # asyncio.gather는 단일 스레드 내 인터리빙이지만, SQLAlchemy Session은
            # 동시 사용을 보장하지 않으므로 호출별로 새 Session을 생성·종료한다.
            if parallel_indexed:
                async def _cs_dispatch(tc: ToolCall) -> tuple:
                    local_db = SessionLocal()
                    try:
                        return await self._timed_dispatch(tc, local_db, user_id, session_id)
                    except Exception:
                        local_db.rollback()
                        raise
                    finally:
                        local_db.close()

                par_results = await asyncio.gather(*(
                    _cs_dispatch(tc) for _, tc in parallel_indexed
                ))
                for (i, _), res in zip(parallel_indexed, par_results):
                    timed_results[i] = res

            # 순차 실행 (Order 에이전트)
            for i, tc in sequential_indexed:
                result = await self._timed_dispatch(tc, db, user_id, session_id)
                # OrderGraph 진행 중 응답이면 즉시 반환
                if isinstance(result[1] if isinstance(result, tuple) else result, _OrderPendingResult):
                    tc_obj, pending, latency_ms = result
                    trace.append(TraceStep(
                        tool=tc_obj.name,
                        arguments=tc_obj.arguments,
                        result=pending.message[:500],
                        iteration=iteration + 1,
                        source="action",
                    ))
                    _log_trace(trace, user_message)
                    return AgentResult(
                        answer=pending.message,
                        intent=_detect_order_action(user_message),
                        escalated=False,
                        tools_used=tools_used,
                        trace=trace,
                        metrics=metrics,
                    )
                timed_results[i] = result

            # trace + metrics 기록 (정상 완료)
            results: list[tuple[ToolCall, str]] = []
            for item in timed_results:
                if item is None:
                    continue
                tc_obj, raw_result, latency_ms = item
                # CS 에이전트는 AgentResult를 반환 — LLM 히스토리용으로 answer 문자열만 추출
                result_str = raw_result.answer if isinstance(raw_result, AgentResult) else raw_result
                results.append((tc_obj, result_str))
                trace.append(TraceStep(
                    tool=tc_obj.name,
                    arguments=tc_obj.arguments,
                    result=result_str[:500],
                    iteration=iteration + 1,
                    source="action",
                ))
                metrics.append(ToolMetricData(
                    tool_name=tc_obj.name,
                    intent=(
                        _detect_order_action(user_message)
                        if tc_obj.name == "call_order_agent"
                        else _SUPERVISOR_TOOL_TO_INTENT.get(tc_obj.name, "other")
                    ),
                    success=True,
                    latency_ms=latency_ms,
                    empty_result=False,
                    iteration=iteration + 1,
                ))
                result_len = len(result_str)
                logger.info(
                    f"[supervisor] iter={iteration+1} tool={tc_obj.name} "
                    f"latency={latency_ms}ms len={result_len} ok=True"
                )

            # ── Pass-through 최적화 ─────────────────────────────────────────
            # 단일 CS 에이전트 호출이면 결과를 그대로 반환 (Supervisor 재합성 LLM 호출 생략)
            # 복합 호출(2개 이상)은 아래 loop로 계속 진행하여 합성
            if len(results) == 1 and results[0][0].name == "call_cs_agent":
                # timed_results에 보존된 AgentResult로 escalated/tools_used/metrics 전파
                _cs_item = next(item for item in timed_results if item is not None)
                _, _cs_full, _ = _cs_item
                _cs_result = _cs_full if isinstance(_cs_full, AgentResult) else None
                answer = _parse_answer(_cs_result.answer if _cs_result else results[0][1])
                intent = _SUPERVISOR_TOOL_TO_INTENT.get(tools_used[0], "other") if tools_used else "other"
                _log_trace(trace, user_message)
                return AgentResult(
                    answer=answer,
                    intent=intent,
                    escalated=_cs_result.escalated if _cs_result else False,
                    tools_used=tools_used + (_cs_result.tools_used if _cs_result else []),
                    trace=trace,
                    metrics=metrics + (_cs_result.metrics if _cs_result else []),
                )

            client.add_tool_results(messages, response, results)

        # 최대 반복 초과
        logger.warning("[supervisor] 최대 반복 초과. 에스컬레이션.")
        _log_trace(trace, user_message)
        return AgentResult(
            answer="요청을 처리하는 데 시간이 걸리고 있습니다. 상담원에게 연결해 드리겠습니다.",
            intent="escalation",
            escalated=True,
            tools_used=tools_used,
            trace=trace,
            metrics=metrics,
        )

    # ── 도구 디스패치 ─────────────────────────────────────────────────────

    async def _timed_dispatch(
        self, tc: ToolCall, db: Session, user_id: int | None, session_id: int | None
    ) -> tuple:
        t0 = time.monotonic()
        result = await self._dispatch_tool(tc, db, user_id, session_id)
        latency_ms = int((time.monotonic() - t0) * 1000)
        return (tc, result, latency_ms)

    async def _dispatch_tool(
        self, tc: ToolCall, db: Session, user_id: int | None, session_id: int | None
    ) -> AgentResult | str | _OrderPendingResult:
        args = tc.arguments
        try:
            match tc.name:
                case "call_cs_agent":
                    # 전체 AgentResult를 반환 — passthrough에서 escalated/tools_used/metrics 전파
                    return await self.cs_executor.run(
                        db=db,
                        user_message=args["query"],
                        user_id=user_id,
                        session_id=session_id,
                        history=[],   # CS 에이전트는 query만 처리
                        input_system=self.cs_input_prompt,
                        output_system=self.cs_output_prompt,
                    )

                case "call_order_agent":
                    if not user_id or not session_id:
                        return "교환·취소 접수는 로그인 후 이용 가능합니다."
                    text = await self._call_order_agent(args["query"], user_id, session_id, db)
                    # _OrderPendingResult로 감싸야 _run_loop에서 즉시 반환 처리됨
                    # str로 반환하면 Supervisor LLM이 메시지를 재구성해버림
                    return _OrderPendingResult(message=text)

                case _:
                    return f"[오류] 알 수 없는 도구: {tc.name}"
        except Exception as e:
            logger.error(f"[supervisor] 도구 실행 오류 ({tc.name}): {e}")
            return f"[오류] {tc.name} 실행 중 문제가 발생했습니다."

    # ── OrderGraph 호출 ───────────────────────────────────────────────────

    async def _has_pending_order_flow(self, session_id: int) -> bool:
        """세션에 진행 중인 OrderGraph 플로우가 있는지 확인."""
        try:
            config = {"configurable": {"thread_id": str(session_id)}}
            snapshot = await self.order_graph.aget_state(config)
            return bool(snapshot.next)
        except Exception:
            return False

    async def _call_order_agent(
        self, query: str, user_id: int, session_id: int, db
    ) -> str:
        """OrderGraph 호출.

        - 진행 중인 플로우: Command(resume=query)로 재개
        - 신규 플로우: 초기 상태로 시작
        반환값: 사용자에게 보여줄 메시지 (질문 또는 완료 메시지)
        """
        from langgraph.types import Command
        from ai.agent.order_graph.state import OrderState

        config = {
            "configurable": {
                "thread_id": str(session_id),
                "db": db,
            }
        }

        try:
            snapshot = await self.order_graph.aget_state(config)
            new_action = _detect_order_action(query)

            # 진행 중인 플로우가 있어도, 사용자가 명시적으로 다른 의도를 표현하면 새 플로우 시작
            pending_action = snapshot.values.get("action") if snapshot.next else None
            intent_mismatch = (
                pending_action is not None
                and pending_action != new_action
                and any(kw in query for kw in _EXCHANGE_KEYWORDS_ACTION | _CANCEL_KEYWORDS_ACTION)
            )

            if snapshot.next and not intent_mismatch:
                # 진행 중인 플로우 재개
                logger.info(f"[order_graph] 플로우 재개 — session={session_id} action={new_action}")
                await self.order_graph.ainvoke(Command(resume=query), config)
            else:
                # 신규 플로우 시작 (또는 의도 불일치로 기존 플로우 교체)
                if intent_mismatch:
                    logger.info(
                        f"[order_graph] 의도 불일치 감지 — 기존={pending_action}, 신규={new_action}. "
                        f"새 플로우로 교체 (session={session_id})"
                    )
                action = new_action
                logger.info(f"[order_graph] 신규 플로우 시작 — session={session_id} action={action}")
                initial_state: OrderState = {
                    "action": action,
                    "user_id": user_id,
                    "session_id": session_id,
                    "user_message": query,
                    "order_id": None,
                    "order_display": None,
                    "selected_items": [],
                    "reason": None,
                    "refund_method": None,
                    "confirmed": None,
                    "abort": False,
                    "ticket_id": None,
                    "response": "",
                    "is_pending": True,
                }
                await self.order_graph.ainvoke(initial_state, config)

            # 실행 후 상태 확인
            new_snapshot = await self.order_graph.aget_state(config)

            if new_snapshot.next and new_snapshot.tasks:
                interrupts = new_snapshot.tasks[0].interrupts
                if interrupts:
                    # 그래프가 interrupt 상태 → 질문을 사용자에게 전달
                    logger.info(f"[order_graph] interrupt 대기 중 — session={session_id}")
                    return str(interrupts[0].value)

            # interrupt 없거나 그래프 완료 → 최종 메시지 반환
            final_state = new_snapshot.values
            response = final_state.get("response", "처리가 완료되었습니다.")
            logger.info(f"[order_graph] 플로우 완료 — session={session_id} ticket={final_state.get('ticket_id')}")
            return response

        except Exception as e:
            logger.error(f"[order_graph] 오류 — session={session_id}: {e}")
            return "주문 처리 중 오류가 발생했습니다. 잠시 후 다시 시도하거나 고객센터로 문의해 주세요."
