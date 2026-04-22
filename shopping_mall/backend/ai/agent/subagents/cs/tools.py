"""CS 에이전트 도구 서브셋 — 조회·안내 전담 (9개).

교환·취소는 OrderGraph가 전담하므로 CS 에이전트에서는 다루지 않습니다.
"""

from ai.agent.tools import TOOL_DEFINITIONS

_CS_TOOL_NAMES: frozenset[str] = frozenset({
    # RAG 도구 (5)
    "search_faq",
    "search_storage_guide",
    "search_season_info",
    "search_policy",
    "search_farm_info",
    # DB 읽기 도구 (3)
    "search_products",
    "get_product_detail",
    "get_order_status",      # 배송 현황 조회 (정책·일반 + 로그인 기반 실제 데이터)
    # 액션 도구 (2)
    "escalate_to_agent",
    "refuse_request",
})

CS_TOOLS: list[dict] = [t for t in TOOL_DEFINITIONS if t["name"] in _CS_TOOL_NAMES]
