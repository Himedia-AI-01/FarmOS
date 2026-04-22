"""에이전트 실행기 — tool_use 루프 + 12개 도구 구현."""
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings

from ai.agent.clients.base import AgentClient, AgentUnavailableError, ToolCall
from ai.agent.tools import TOOL_DEFINITIONS, TOOL_TO_INTENT

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 10       # 하드코딩 폴백 (settings 미설정 시)
MAX_ANSWER_LENGTH = 1000  # 최종 응답 최대 글자 수
_WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]

# 한국어 문자 바로 뒤의 "; " 패턴 — 코드·URL·세미콜론 리스트를 건드리지 않도록
# 한국어 문자(U+AC00–U+D7A3) 뒤에 등장하는 세미콜론+공백만 대상으로 삼음
_KO_SEMICOLON_RE = re.compile(r"(?<=[\uAC00-\uD7A3]);\s+")

# 배송/주문 상태 한국어 매핑
_SHIPMENT_STATUS_KO: dict[str, str] = {
    "registered": "배송 준비 중",
    "picked_up":  "배송 중 (픽업 완료)",
    "in_transit": "배송 중",
    "delivered":  "배송 완료",
}
_ORDER_STATUS_KO: dict[str, str] = {
    "pending":    "주문 접수",
    "registered": "배송 준비 중",
    "shipping":   "배송 중",
    "delivered":  "배송 완료",
    "cancelled":  "취소 완료",
}

# DB 세션이 필요 없는 RAG 전용 도구 — asyncio.gather로 병렬 실행 가능
_RAG_TOOLS: frozenset[str] = frozenset({
    "search_faq",
    "search_storage_guide",
    "search_season_info",
    "search_policy",
    "search_farm_info",
})

# 로그인 필요 도구가 비로그인 상태로 호출될 때 반환하는 사전 정의 메시지.
# LLM이 이 문자열을 받으면 재가공하지 않고 그대로 사용자에게 전달됩니다.
_LOGIN_REQUIRED_RESPONSE = (
    "주문 내역 조회는 로그인 후 이용하실 수 있는 서비스예요. "
    "로그인하신 뒤 다시 이용해 주세요."
)


# ── 요청 컨텍스트 ──────────────────────────────────────────────────────────────

@dataclass
class RequestContext:
    """LLM에 주입할 요청 시점의 세션 상태."""
    user_id: int | None
    is_logged_in: bool
    current_date: str   # "2026-04-13"
    current_time: str   # "14:32"

    @classmethod
    def build(cls, user_id: int | None) -> "RequestContext":
        now = datetime.now(timezone.utc).astimezone()
        return cls(
            user_id=user_id,
            is_logged_in=user_id is not None,
            current_date=now.strftime("%Y-%m-%d"),
            current_time=now.strftime("%H:%M"),
        )

    def to_system_suffix(self) -> str:
        """시스템 프롬프트 끝에 붙일 컨텍스트 블록."""
        login_status = "로그인" if self.is_logged_in else "비로그인"
        return (
            f"\n\n## 현재 요청 컨텍스트\n"
            f"- 날짜/시각: {self.current_date} {self.current_time}\n"
            f"- 사용자 상태: {login_status}\n"
            f"- 주문 조회 가능: {'예' if self.is_logged_in else '아니오 (로그인 필요)'}"
        )

# policy_type → ChromaDB 컬렉션명
POLICY_COLLECTIONS: dict[str, list[str]] = {
    "return":     ["return_policy"],
    "payment":    ["payment_policy"],
    "membership": ["membership_policy"],
    "delivery":   ["delivery_policy"],
    "quality":    ["quality_policy"],
    "service":    ["service_policy"],
    "all": [
        "return_policy", "payment_policy", "membership_policy",
        "delivery_policy", "quality_policy", "service_policy",
    ],
}


_TOOL_SOURCE: dict[str, str] = {
    "search_faq": "rag",
    "search_storage_guide": "rag",
    "search_season_info": "rag",
    "search_policy": "rag",
    "search_farm_info": "rag",
    "get_order_status": "db",
    "search_products": "db",
    "get_product_detail": "db",
    "escalate_to_agent": "action",
    "refuse_request": "action",
}


@dataclass
class TraceStep:
    """도구 호출 한 단계의 추론 기록."""
    tool: str
    arguments: dict
    result: str        # 도구 실행 결과 (최대 500자)
    iteration: int     # 루프 몇 번째 반복
    source: str = "rag"  # "rag" | "db" | "action" | "parametric"


@dataclass
class ToolMetricData:
    """도구 호출 1건의 성능/품질 메트릭."""
    tool_name: str
    intent: str
    success: bool
    latency_ms: int
    empty_result: bool
    iteration: int


# ── 빈 결과 판별 ──────────────────────────────────────────────────────────────
# 단순 부분 문자열("없습니다" 등)은 정상 응답에서도 오탐이 발생하므로,
# 빈 결과를 명확히 나타내는 완전한 구(句) 단위의 정규식만 사용한다.

_EMPTY_RESULT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"찾을\s*수\s*없습니다"),        # "~을 찾을 수 없습니다"
    re.compile(r"결과가\s*없습니다"),            # "검색 결과가 없습니다"
    re.compile(r"조회된\s*주문이\s*없습니다"),   # "조회된 주문이 없습니다"
    re.compile(r"정보를\s*찾을\s*수\s*없습니다"), # "정보를 찾을 수 없습니다"
    re.compile(r"검색\s*결과가\s*없습니다"),     # "검색 결과가 없습니다"
)


def _is_empty_result(result: str) -> bool:
    """도구 결과가 사실상 '빈 결과'인지 판별.

    공백·줄바꿈을 정규화한 뒤 완전한 구 단위 정규식으로만 매칭하여
    정상 응답에 포함된 "없습니다"로 인한 오탐을 방지한다.
    """
    normalized = re.sub(r"\s+", " ", result).strip()
    return any(p.search(normalized) for p in _EMPTY_RESULT_PATTERNS)


@dataclass
class AgentResult:
    answer: str
    intent: str
    escalated: bool
    tools_used: list[str] = field(default_factory=list)
    trace: list[TraceStep] = field(default_factory=list)
    metrics: list[ToolMetricData] = field(default_factory=list)


# ── 응답 후처리 ────────────────────────────────────────────────────────────────

def _parse_answer(raw: str) -> str:
    """LLM 응답 텍스트 후처리.

    - 마크다운 헤딩(#) 제거
    - 과도한 빈 줄 압축 (3줄 이상 → 2줄)
    - 최대 길이 초과 시 문장 단위로 자름
    """
    # 마크다운 헤딩 제거 (## 제목 → 제목)
    text = re.sub(r"^#{1,6}\s+", "", raw, flags=re.MULTILINE)
    # LLM이 한국어 문장을 세미콜론으로 이어붙이는 오류 보정 ("가나다; 라마바" → "가나다. 라마바")
    # 코드 펜스(```) 밖에서만, 한국어 문자 바로 뒤의 "; " 패턴에만 적용
    _fence_parts = text.split("```")
    text = "```".join(
        _KO_SEMICOLON_RE.sub(". ", seg) if i % 2 == 0 else seg
        for i, seg in enumerate(_fence_parts)
    )
    # 3줄 이상 연속 빈 줄 → 2줄
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) <= MAX_ANSWER_LENGTH:
        return text

    # 문장 단위 자르기 (마침표/느낌표/물음표 기준)
    truncated = text[:MAX_ANSWER_LENGTH]
    last_sentence_end = max(
        truncated.rfind("다."),
        truncated.rfind("요."),
        truncated.rfind("!"),
        truncated.rfind("?"),
    )
    if last_sentence_end > MAX_ANSWER_LENGTH // 2:
        truncated = truncated[: last_sentence_end + 1]

    return truncated + "\n\n(이어지는 내용은 상담원에게 문의해 주세요.)"


def _log_trace(trace: "list[TraceStep]", question: str) -> None:
    """추론 과정을 INFO 레벨로 출력."""
    if not trace:
        logger.info(f"[trace] 질문='{question[:60]}' → 도구 호출 없음 (직접 답변)")
        return
    logger.info(f"[trace] 질문='{question[:60]}' → {len(trace)}단계 도구 호출")
    for step in trace:
        logger.info(f"  [{step.iteration}] {step.tool}({step.arguments}) [{step.source}]")


class AgentExecutor:
    """Primary → Fallback tool_use 루프 실행기."""

    def __init__(
        self,
        primary: AgentClient,
        fallback: AgentClient | None,
        rag_service,
        tools: list[dict] | None = None,
        max_iterations: int | None = None,
    ):
        self.primary = primary
        self.fallback = fallback
        self.rag = rag_service
        self.tools = tools or TOOL_DEFINITIONS
        self.max_iterations = max_iterations or settings.agent_max_iterations or MAX_ITERATIONS

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
        """에이전트 루프 실행. Primary 실패 시 Fallback으로 전환."""
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
            logger.warning(f"Primary LLM 실패: {e}. Fallback 시도.")
            if self.fallback:
                try:
                    return await self._run_loop(
                        self.fallback, db, user_message, user_id, session_id, history,
                        input_with_ctx, output_with_ctx,
                    )
                except AgentUnavailableError as e2:
                    logger.error(f"Fallback LLM도 실패: {e2}")
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

        for iteration in range(self.max_iterations):
            response = await client.chat_with_tools(messages, self.tools, input_system)

            # 도구 호출 없음 → 최종 답변 생성
            if not response.tool_calls:
                if tools_used:
                    # 도구 결과가 있으면 output_system으로 응답 합성
                    synth = await client.chat_with_tools(messages, [], output_system)
                    raw_answer = synth.text or "죄송합니다. 답변을 생성하지 못했습니다."
                else:
                    # 도구 없이 직접 답변 (엣지 케이스)
                    raw_answer = response.text or "죄송합니다. 답변을 생성하지 못했습니다."
                answer = _parse_answer(raw_answer)
                intent = TOOL_TO_INTENT.get(tools_used[0], "other") if tools_used else "other"

                # RAG 도구 결과가 없거나 도구 자체를 쓰지 않은 경우 → LLM 자체 지식 사용
                # 에스컬레이션은 상담원에게 넘기는 것이므로 parametric 보완과 무관
                has_empty_rag = any(
                    _is_empty_result(s.result) and s.source == "rag"
                    for s in trace
                )
                if not escalated and (not trace or has_empty_rag):
                    trace.append(TraceStep(
                        tool="_parametric_fallback",
                        arguments={},
                        result="RAG 결과 없음 — LLM 사전 지식으로 보완",
                        iteration=iteration + 1,
                        source="parametric",
                    ))

                _log_trace(trace, user_message)
                return AgentResult(
                    answer=answer,
                    intent=intent,
                    escalated=escalated,
                    tools_used=tools_used,
                    trace=trace,
                    metrics=metrics,
                )

            # 실행 전 메타데이터 수집 (순서 보장)
            for tc in response.tool_calls:
                tools_used.append(tc.name)
                if tc.name == "escalate_to_agent":
                    escalated = True

            # RAG 도구는 병렬, DB/액션 도구는 순차 실행 (동일 Session 공유)
            rag_indexed = [
                (i, tc) for i, tc in enumerate(response.tool_calls)
                if tc.name in _RAG_TOOLS
            ]
            db_indexed = [
                (i, tc) for i, tc in enumerate(response.tool_calls)
                if tc.name not in _RAG_TOOLS
            ]

            timed_results: list[tuple[ToolCall, str, int]] = [None] * len(response.tool_calls)  # type: ignore[list-item]

            if rag_indexed:
                rag_results = await asyncio.gather(*(
                    self._timed_dispatch(tc, db, user_id, session_id)
                    for _, tc in rag_indexed
                ))
                for (i, _), res in zip(rag_indexed, rag_results):
                    timed_results[i] = res

            for i, tc in db_indexed:
                timed_results[i] = await self._timed_dispatch(tc, db, user_id, session_id)

            # trace + metrics 기록 (원래 순서)
            results: list[tuple[ToolCall, str]] = []
            for tc, result, latency_ms in timed_results:
                success = not result.startswith("[오류]") and "오류가 발생했습니다" not in result
                empty = _is_empty_result(result)
                intent_for_metric = TOOL_TO_INTENT.get(tc.name, "other")

                results.append((tc, result))
                trace.append(TraceStep(
                    tool=tc.name,
                    arguments=tc.arguments,
                    result=result[:500],
                    iteration=iteration + 1,
                    source=_TOOL_SOURCE.get(tc.name, "rag"),
                ))
                metrics.append(ToolMetricData(
                    tool_name=tc.name,
                    intent=intent_for_metric,
                    success=success,
                    latency_ms=latency_ms,
                    empty_result=empty,
                    iteration=iteration + 1,
                ))
                logger.info(
                    f"[trace] iter={iteration+1} tool={tc.name} "
                    f"args={tc.arguments} latency={latency_ms}ms → {result[:120]}"
                )

            # 로그인 필요 단락: 사전 정의 메시지를 그대로 반환 (LLM 재가공 없이)
            if len(results) == 1 and results[0][1] == _LOGIN_REQUIRED_RESPONSE:
                _log_trace(trace, user_message)
                return AgentResult(
                    answer=_LOGIN_REQUIRED_RESPONSE,
                    intent=TOOL_TO_INTENT.get(tools_used[0], "other") if tools_used else "other",
                    escalated=False,
                    tools_used=tools_used,
                    trace=trace,
                    metrics=metrics,
                )

            client.add_tool_results(messages, response, results)

        # 최대 반복 초과 → 에스컬레이션
        logger.warning("에이전트 최대 반복 초과. 에스컬레이션.")
        _log_trace(trace, user_message)
        return AgentResult(
            answer="요청을 처리하는 데 시간이 걸리고 있습니다. 상담원에게 연결해 드리겠습니다.",
            intent=TOOL_TO_INTENT.get(tools_used[0], "other") if tools_used else "escalation",
            escalated=True,
            tools_used=tools_used,
            trace=trace,
            metrics=metrics,
        )

    # ── 도구 디스패치 ─────────────────────────────────────────────────────

    async def _timed_dispatch(
        self, tc: ToolCall, db: Session, user_id: int | None, session_id: int | None = None
    ) -> tuple[ToolCall, str, int]:
        """도구 실행 + 소요 시간 측정."""
        t0 = time.monotonic()
        result = await self._dispatch_tool(tc, db, user_id, session_id)
        latency_ms = int((time.monotonic() - t0) * 1000)
        return (tc, result, latency_ms)

    async def _dispatch_tool(
        self, tc: ToolCall, db: Session, user_id: int | None, session_id: int | None = None
    ) -> str:
        args = tc.arguments
        try:
            match tc.name:
                case "search_faq":
                    return await self._tool_search_faq(**args)
                case "search_storage_guide":
                    return await self._tool_search_storage_guide(**args)
                case "search_season_info":
                    return await self._tool_search_season_info(**args)
                case "search_policy":
                    return await self._tool_search_policy(**args)
                case "get_order_status":
                    # LLM이 user_id를 인자로 넘기면 타인 정보 조회 시도로 간주 — 즉시 거절.
                    # get_order_status 도구 스키마에 user_id 파라미터가 없으므로,
                    # LLM이 이를 명시하는 경우는 사용자가 특정 user_id를 요청한 것입니다.
                    if "user_id" in args:
                        logger.warning(
                            "get_order_status에 user_id 인자 감지 — 타인 정보 조회 시도로 거절 (redacted)"
                        )
                        return self._tool_refuse_request("other_user_info")
                    return await self._tool_get_order_status(db, user_id, **args)
                case "search_products":
                    return await self._tool_search_products(db, **args)
                case "get_product_detail":
                    return await self._tool_get_product_detail(db, **args)
                case "search_farm_info":
                    return await self._tool_search_farm_info(**args)
                case "escalate_to_agent":
                    return self._tool_escalate_to_agent(**args)
                case "refuse_request":
                    return self._tool_refuse_request(**args)
                case _:
                    return f"[오류] 알 수 없는 도구: {tc.name}"
        except Exception as e:
            logger.error(f"도구 실행 오류 ({tc.name}): {e}")
            return f"[오류] {tc.name} 실행 중 문제가 발생했습니다."

    # ── 도구 구현 ─────────────────────────────────────────────────────────

    async def _tool_search_faq(self, query: str, top_k: int = 3) -> str:
        docs = self.rag.retrieve(query, "faq", top_k=top_k, distance_threshold=0.45)
        if not docs:
            return "FAQ에서 관련 내용을 찾을 수 없습니다."
        return "\n\n".join(docs)

    async def _tool_search_storage_guide(self, product_name: str, query: str) -> str:
        docs = self.rag.retrieve(
            query, "storage_guide", top_k=3, distance_threshold=0.40,
            where={"product_name": product_name} if product_name else None,
        )
        if not docs:
            # 메타데이터 필터 없이 재시도
            docs = self.rag.retrieve(query, "storage_guide", top_k=3, distance_threshold=0.45)
        if not docs:
            return f"'{product_name}' 보관법 정보를 찾을 수 없습니다."
        return "\n\n".join(docs)

    async def _tool_search_season_info(self, query: str, season: str | None = None) -> str:
        where = {"season": season} if season else None
        docs = self.rag.retrieve(query, "season_info", top_k=3, distance_threshold=0.45, where=where)
        if not docs:
            return "제철 정보를 찾을 수 없습니다."
        return "\n\n".join(docs)

    async def _tool_search_policy(self, query: str, policy_type: str = "all") -> str:
        collections = POLICY_COLLECTIONS.get(policy_type, POLICY_COLLECTIONS["all"])
        # ko-sroberta-multitask 모델 실측: 정책 문서의 관련 청크 거리가 0.51~0.62 대에 형성됨
        # → 기본 0.50은 모든 결과를 차단하므로 0.65로 상향 조정
        # top_k_per=3: 단일 컬렉션 조회 시 주요 조항을 충분히 포함
        docs = self.rag.retrieve_multiple(query, collections, top_k_per=3, distance_threshold=0.65)
        if not docs:
            return "관련 정책 정보를 찾을 수 없습니다."
        return "\n\n".join(docs)

    async def _tool_get_order_status(
        self, db: Session, user_id: int | None, order_id: int | None = None, **_
    ) -> str:
        if not user_id:
            return _LOGIN_REQUIRED_RESPONSE

        from app.models.order import Order
        from app.models.shipment import Shipment

        try:
            base_query = db.query(Order).filter(Order.user_id == user_id)
            total_orders = base_query.count()

            if order_id:
                orders = base_query.filter(Order.id == order_id).all()
            else:
                orders = base_query.order_by(Order.created_at.desc()).limit(3).all()

            if not orders:
                return "조회된 주문이 없습니다."

            # LLM이 "다른 주문이 없다"는 사실을 알 수 있도록 전체 주문 수를 헤더로 제공
            header = f"[이 사용자의 전체 주문: {total_orders}건 / 아래 최근 {len(orders)}건 표시]\n\n"

            parts = []
            for order in orders:
                shipment = db.query(Shipment).filter(Shipment.order_id == order.id).first()
                items_summary = ", ".join(
                    f"{item.product.name} x{item.quantity}"
                    for item in order.items
                    if item.product
                )
                order_status_ko = _ORDER_STATUS_KO.get(order.status, order.status)
                part = (
                    f"주문번호: #{order.id}\n"
                    f"주문일: {order.created_at.strftime('%Y-%m-%d')}\n"
                    f"상품: {items_summary or '정보 없음'}\n"
                    f"금액: {order.total_price:,}원\n"
                    f"주문상태: {order_status_ko}"
                )
                if shipment:
                    shipment_status_ko = _SHIPMENT_STATUS_KO.get(shipment.status, shipment.status)
                    part += (
                        f"\n택배사: {shipment.carrier}"
                        f"\n송장번호: {shipment.tracking_number}"
                        f"\n배송상태: {shipment_status_ko}"
                    )
                    if shipment.expected_arrival:
                        arrival = await self._adjust_arrival_date(shipment.expected_arrival)
                        part += arrival
                else:
                    part += "\n배송정보: 아직 등록되지 않았습니다"
                parts.append(part)

            return header + "\n\n---\n\n".join(parts)

        except Exception as e:
            logger.error(f"주문 조회 오류: {e}")
            return "주문 조회 중 오류가 발생했습니다."

    async def _adjust_arrival_date(self, raw_arrival: datetime) -> str:
        """expected_arrival을 공휴일/주말 기준으로 조정하여 문자열로 반환.

        공공데이터포털 API 키가 없으면 원본 날짜를 그대로 반환합니다.
        """
        api_key = settings.anniversary_api_key
        arrival_date = raw_arrival.date() if isinstance(raw_arrival, datetime) else raw_arrival

        if not api_key:
            return f"\n도착예정: {arrival_date.strftime('%Y-%m-%d')}"

        try:
            from ai.agent.holiday import next_business_day
            adjusted, skipped = await next_business_day(arrival_date, api_key)

            if not skipped:
                return f"\n도착예정: {adjusted.strftime('%Y-%m-%d')} ({_WEEKDAY_KO[adjusted.weekday()]}요일)"

            skip_summary = ", ".join(skipped)
            return (
                f"\n도착예정: {adjusted.strftime('%Y-%m-%d')} "
                f"(원래 {arrival_date.strftime('%Y-%m-%d')}이었으나 {skip_summary} 제외하여 조정)"
            )
        except Exception as e:
            logger.warning(f"영업일 조정 실패: {e}")
            return f"\n도착예정: {arrival_date.strftime('%Y-%m-%d')}"

    async def _tool_search_products(
        self, db: Session, query: str, check_stock: bool = False, limit: int = 5
    ) -> str:
        limit = max(1, min(limit, 20))  # LLM이 넘긴 limit을 1~20 범위로 제한
        from app.models.product import Product

        try:
            q = db.query(Product).filter(Product.name.ilike(f"%{query}%"))
            if check_stock:
                q = q.filter(Product.stock > 0)
            products = q.order_by(Product.sales_count.desc()).limit(limit).all()

            if not products:
                return f"'{query}' 검색 결과가 없습니다."

            lines = []
            for p in products:
                discounted = int(p.price * (1 - p.discount_rate / 100)) if p.discount_rate else p.price
                stock_info = f"재고 {p.stock}개" if p.stock > 0 else "품절"
                line = f"- [{p.id}] {p.name} / {discounted:,}원"
                if p.discount_rate:
                    line += f" (할인율 {p.discount_rate}%)"
                line += f" / {stock_info} / 평점 {p.rating:.1f}"
                lines.append(line)

            return f"'{query}' 검색 결과 ({len(products)}건):\n" + "\n".join(lines)

        except Exception as e:
            logger.error(f"상품 검색 오류: {e}")
            return "상품 검색 중 오류가 발생했습니다."

    async def _tool_get_product_detail(
        self, db: Session, product_id: int | None = None, product_name: str | None = None
    ) -> str:
        from app.models.product import Product

        try:
            if product_id:
                product = db.query(Product).filter(Product.id == product_id).first()
            elif product_name:
                product = db.query(Product).filter(
                    Product.name.ilike(f"%{product_name}%")
                ).first()
            else:
                return "상품 ID 또는 상품명을 입력해 주세요."

            if not product:
                return "해당 상품을 찾을 수 없습니다."

            discounted = int(product.price * (1 - product.discount_rate / 100)) if product.discount_rate else product.price
            stock_status = f"{product.stock}개 재고" if product.stock > 0 else "품절"
            if product.stock == 0 and product.restock_date:
                stock_status += f" (입고 예정: {product.restock_date.strftime('%Y-%m-%d')})"

            return (
                f"상품명: {product.name}\n"
                f"가격: {discounted:,}원"
                + (f" (정가 {product.price:,}원, {product.discount_rate}% 할인)" if product.discount_rate else "") + "\n"
                f"재고: {stock_status}\n"
                f"평점: {product.rating:.1f}점 ({product.review_count}개 리뷰)\n"
                f"누적 판매: {product.sales_count}건\n"
                + (f"설명: {product.description}\n" if product.description else "")
            )

        except Exception as e:
            logger.error(f"상품 상세 조회 오류: {e}")
            return "상품 정보 조회 중 오류가 발생했습니다."

    async def _tool_search_farm_info(self, query: str) -> str:
        docs = self.rag.retrieve(query, "farm_intro", top_k=3, distance_threshold=0.50)
        if not docs:
            return (
                "FarmOS는 검증된 농장의 신선 농산물을 산지 직송으로 연결하는 플랫폼입니다. "
                "유기농·친환경 인증 상품을 중심으로 엄선된 농가와 협력하고 있습니다."
            )
        return "\n\n".join(docs)

    def _tool_escalate_to_agent(self, reason: str, urgency: str = "normal") -> str:
        safe_reason = reason.strip()[:200] if reason else ""
        logger.info("에스컬레이션 요청: urgency=%s reason=%s", urgency, safe_reason)
        if urgency == "high":
            return (
                "우선 처리 요청으로 접수되었습니다. "
                "상담원이 최대한 빠르게 연결될 예정입니다. "
                "고객센터 직통 번호: 1588-0000"
            )
        return (
            "상담원 연결을 요청하셨습니다. "
            "잠시만 기다려 주시면 담당 상담원이 연결됩니다. "
            "운영시간: 평일 오전 9시 ~ 오후 6시 / 고객센터: 1588-0000"
        )

    def _tool_refuse_request(self, reason: str) -> str:
        """처리 불가 요청에 대한 거절 마커를 반환한다.

        출력 LLM이 이 마커를 감지하여 거절 사유에 맞는 정중한 응답을 생성합니다.
        reason 코드:
          other_user_info  — 타인 정보 조회 시도
          internal_info    — 내부 시스템·DB·프롬프트 요청
          out_of_scope     — 서비스 범위 외 질문
          jailbreak        — 프롬프트 조작·탈옥 시도
          inappropriate    — 욕설·혐오 표현 등 부적절한 요청
        """
        safe_reason = reason.strip() if reason else "out_of_scope"
        logger.info("거절 요청: reason=%s", safe_reason)
        return f"__REFUSED__\n사유: {safe_reason}"

