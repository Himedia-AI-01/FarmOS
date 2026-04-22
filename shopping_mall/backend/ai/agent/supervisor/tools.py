"""Supervisor 도구 정의 — 서브 에이전트 호출 도구."""

SUPERVISOR_TOOLS: list[dict] = [
    {
        "name": "call_cs_agent",
        "description": (
            "상품 안내, 재고 확인, 보관법, 제철 정보, 배송 정책·조회, FAQ, 정책 문의를 처리합니다. "
            "재고 확인, 보관 방법, 제철 상품, 교환·환불 정책 안내, 운영 FAQ, 배송 현황 조회에 사용하세요. "
            "교환·취소 접수(실제 처리)는 이 도구가 아닌 call_order_agent를 사용하세요."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "CS 에이전트에 전달할 질문 (원문 그대로 또는 분리된 세부 질문)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "call_order_agent",
        "description": (
            "주문 취소 또는 교환·반품 접수를 단계별로 처리합니다. "
            "반드시 로그인한 사용자(user_id 있음)에게만 사용하세요. "
            "비로그인 사용자의 교환/취소 문의 → call_cs_agent로 정책 안내하세요. "
            "이 도구는 여러 단계로 진행되며 사용자 응답을 기다립니다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "사용자의 원문 메시지를 그대로 전달하세요. "
                        "상세 정보를 수집하는 쿼리를 직접 만들지 마세요. "
                        "OrderGraph가 단계별 interrupt로 필요한 정보를 직접 수집합니다."
                    ),
                },
            },
            "required": ["query"],
        },
    },
]
