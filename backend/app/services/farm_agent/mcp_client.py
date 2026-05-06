"""외부 MCP 서버 → LangChain 도구 어댑터.

설계:
  - JSON 환경변수(MCP_SERVERS_JSON) 한 줄 설정으로 서버 추가 가능.
  - lifespan에서 1회 로드 → orchestrator의 tools=[]에 주입.
  - Deep Agents의 알려진 제약: 서브에이전트는 MCP 도구에 직접 접근하지 못한다.
    따라서 MCP 도구는 오케스트레이터 레벨에 둬서 task 위임 없이 직접 호출하게 한다.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def load_mcp_tools(servers_json: str) -> tuple[list[Any], Any | None]:
    """외부 MCP 서버에서 LangChain 도구 목록을 로드한다.

    Args:
        servers_json: MCP_SERVERS_JSON 환경변수 값. 빈 문자열이면 비활성.

    Returns:
        (tools, client) — client는 lifespan 종료 시 정리용. 실패하면 ([], None).
    """
    if not (servers_json or "").strip():
        return [], None

    try:
        config = json.loads(servers_json)
    except json.JSONDecodeError as e:
        logger.warning("MCP_SERVERS_JSON 파싱 실패: %s", e)
        return [], None

    if not isinstance(config, dict) or not config:
        logger.warning("MCP_SERVERS_JSON 형식 오류 (dict 필요)")
        return [], None

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        logger.warning("langchain-mcp-adapters 미설치 — MCP 도구 비활성")
        return [], None

    try:
        client = MultiServerMCPClient(config)
        tools = await client.get_tools()
        logger.info(
            "mcp.tools_loaded servers=%s count=%d",
            list(config.keys()),
            len(tools),
        )
        return list(tools), client
    except Exception as exc:  # noqa: BLE001 — MCP 실패가 서버 기동을 막지 않음
        logger.warning("mcp.load_failed err=%s", exc, exc_info=True)
        return [], None
