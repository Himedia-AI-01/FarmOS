"""CS 에이전트 도구 서브셋 — 조회·안내·거절 전담 (10개).

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

# 초기화 검증 — _CS_TOOL_NAMES에 있지만 TOOL_DEFINITIONS에 없는 이름을 즉시 탐지.
# 오타나 TOOL_DEFINITIONS 미등록으로 CS_TOOLS가 조용히 누락되는 것을 방지합니다.
_defined_names: frozenset[str] = frozenset(t["name"] for t in TOOL_DEFINITIONS)
_missing: frozenset[str] = _CS_TOOL_NAMES - _defined_names
if _missing:
    raise ValueError(
        f"_CS_TOOL_NAMES에 TOOL_DEFINITIONS에 없는 도구 이름이 포함되어 있습니다: {_missing}. "
        f"CS_TOOLS에서 해당 도구가 누락됩니다. "
        f"TOOL_DEFINITIONS에 도구를 추가하거나 _CS_TOOL_NAMES에서 잘못된 이름을 제거하세요."
    )
