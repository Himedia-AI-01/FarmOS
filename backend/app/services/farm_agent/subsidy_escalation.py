"""Subsidy 에이전트 자동 escalation: Sonnet → Opus on UNCLEAR.

배경: Subsidy 답변은 농민의 직불금 자격에 직접 영향. 잘못된 "필수" 단정은 부정수급
판정으로 10% 감액 → 농민 손해. Sonnet 4.6 가 평소엔 충분하지만 시행지침 검색이
약한 신호로 끝나는 hard case (verdict_hint=UNCLEAR, RAG 유사도 낮음) 에서는
Opus 4.7 로 재시도해 칼리브레이션을 끌어올린다.

설계:
  1. Sonnet 으로 첫 시도 (~$0.08/query 평균)
  2. 응답 텍스트에 UNCLEAR 마커 ("⚠️ 시행지침상 명확한", "확인을 권장", "UNCLEAR")
     포함 시 Opus 로 재시도 (+$0.19/query — 5-10% 의 query 에서만 발생)
  3. Opus 응답을 최종 반환

LangGraph StateGraph 로 구현해 deepagents `CompiledSubAgent` 슬롯에 그대로 등록.
이 모듈을 import 하면 build_subsidy_agent_with_escalation() 가 (graph, description)
튜플을 반환 — agent.py 가 등록 시 사용.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import create_react_agent

from app.services.farm_agent.models import build_llm_for
from app.services.farm_agent.prompts import SUBSIDY_PROMPT

logger = logging.getLogger(__name__)


# Compiled-graph cache. ChatOpenAI + create_react_agent + StateGraph.compile()
# are non-trivial to construct (HTTP client, prompt baking, runtime
# reflection). Building this per request leaks file descriptors and adds
# 50–200ms latency to the subsidy path. Cache by tool-identity tuple so
# distinct tool sets get distinct graphs.
_compiled_graph_cache: dict[tuple[int, ...], Any] = {}


# UNCLEAR 마커 — SUBSIDY_PROMPT 의 calibration 규칙에서 정의된 표현 + verdict_hint 키.
# 어느 하나라도 응답에 등장하면 escalation 트리거.
#
# Removed: "직불 담당 (시·군·읍면동)" 단독 매칭. 이 문구는 MANDATORY 답변에서도
# "기타 문의는 담당자에게" footer 로 자주 등장 → 잘못된 Opus escalation 으로 추가
# $$$ 비용을 만들었다. 이제 footer 문구는 UNCLEAR-context 에 동반 등장할 때만 트리거.
_UNCLEAR_MARKERS = (
    "⚠️ 시행지침상 명확한",  # SUBSIDY_PROMPT 가 UNCLEAR 케이스에서 강제하는 정확 표현
    "명확한 의무 조항을 찾지 못",
    "UNCLEAR",  # tool 결과의 verdict_hint 가 그대로 답변에 노출된 경우
    "정확한 조항 못 찾았",  # SUBSIDY_PROMPT critique-refine 절차의 fallback 문구
    "별도 조항을 찾지 못",
    "확인하지 못했습니다",  # honest "RAG 미적중" 답변 마커
    "검색 결과에 나타나지 않",
)

_ESCALATION_PROMPT_SUFFIX = """\

## [ESCALATION 모드]
이전 라운드에서 시행지침 검색이 약한 신호 (UNCLEAR) 로 끝났습니다.
당신은 본 라운드에서 더 깊이 분석합니다:
1. 검색 결과 인용을 한 번 더 점검 — 가장 유사도 높은 인용에 의무 법령 동사
   ("하여야 한다", "준수사항", "감액") 가 있으면 그 인용을 근거로 단정.
2. 8대 준수사항 키워드 매칭 (영농기록·경영체등록·농약·비료·폐기물·교육·마을·농지)
   재확인 — 매칭되면 즉시 MANDATORY.
3. 그래도 신호가 명확하지 않으면 이전 라운드의 결론 그대로 ("⚠️ 명확한 조항을
   찾지 못함, 담당 부서 문의 권장") 단, 어떤 검색을 시도했는지 1줄 요약을 추가.

이전 라운드의 도구 호출 결과는 messages 컨텍스트에 그대로 남아있습니다 — 동일 검색
재실행 대신 다른 키워드로 보완 검색을 시도하세요.
"""


def _is_unclear(text: str) -> bool:
    """응답이 UNCLEAR 마커를 포함하는지 검사."""
    if not text:
        return False
    return any(marker in text for marker in _UNCLEAR_MARKERS)


def build_subsidy_agent_with_escalation(tools: Sequence[Any]) -> Any:
    """Sonnet → Opus 자동 escalation 을 수행하는 LangGraph CompiledStateGraph 반환.

    deepagents CompiledSubAgent 슬롯에 등록 가능. 노드 구성:
      START → sonnet → [UNCLEAR 검사] → opus → END
                                    ↘ END (UNCLEAR 아니면 sonnet 응답 그대로)

    각 노드는 create_react_agent 를 호출해 해당 모델의 tool-use 능력을 그대로 활용.

    Returns a cached compiled graph keyed on tool identity so repeated calls
    don't reinstantiate the underlying ChatOpenAI HTTP clients.
    """

    cache_key = tuple(id(t) for t in tools)
    cached = _compiled_graph_cache.get(cache_key)
    if cached is not None:
        return cached

    sonnet_llm = build_llm_for("subsidy", max_tokens=4096)
    opus_llm = build_llm_for("subsidy_escalation", max_tokens=4096)

    sonnet_agent = create_react_agent(
        sonnet_llm,
        tools=list(tools),
        prompt=SystemMessage(content=SUBSIDY_PROMPT),
    )
    opus_agent = create_react_agent(
        opus_llm,
        tools=list(tools),
        prompt=SystemMessage(content=SUBSIDY_PROMPT + _ESCALATION_PROMPT_SUFFIX),
    )

    async def sonnet_node(state: MessagesState) -> dict:
        result = await sonnet_agent.ainvoke({"messages": state["messages"]})
        return {"messages": result["messages"]}

    async def opus_node(state: MessagesState) -> dict:
        # state.messages 에 sonnet 라운드의 tool calls + 결과가 그대로 들어있어 opus 가
        # 이전 검색 컨텍스트를 보고 다른 각도로 보완할 수 있다.
        last_human_idx = next(
            (i for i in reversed(range(len(state["messages"])))
             if isinstance(state["messages"][i], HumanMessage)),
            0,
        )
        # opus 가 새 라운드를 의식하도록 ESCALATION 한 줄을 마지막 사용자 메시지로 추가.
        boost = HumanMessage(content="[ESCALATION] 위 답변이 UNCLEAR 였습니다. 더 깊이 분석해 주세요.")
        new_messages = list(state["messages"]) + [boost]
        result = await opus_agent.ainvoke({"messages": new_messages})
        logger.info("subsidy.escalation.opus_invoked human_idx=%d", last_human_idx)
        return {"messages": result["messages"]}

    def route_after_sonnet(state: MessagesState) -> str:
        last = state["messages"][-1] if state["messages"] else None
        text = getattr(last, "content", "") or ""
        if _is_unclear(text):
            logger.info("subsidy.escalation.triggered markers_detected=true")
            return "opus"
        return END

    graph = StateGraph(MessagesState)
    graph.add_node("sonnet", sonnet_node)
    graph.add_node("opus", opus_node)
    graph.add_edge(START, "sonnet")
    graph.add_conditional_edges("sonnet", route_after_sonnet, {"opus": "opus", END: END})
    graph.add_edge("opus", END)
    compiled = graph.compile()
    _compiled_graph_cache[cache_key] = compiled
    return compiled
