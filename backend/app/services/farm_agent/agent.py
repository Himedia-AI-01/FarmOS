"""FarmOS Deep Agent 빌더.

create_deep_agent(model, tools, system_prompt, subagents, checkpointer)
  → CompiledStateGraph (.ainvoke / .astream)

오케스트레이터는 직접 도구를 갖지 않고 task로 서브에이전트에 위임한다.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.core.config import settings
from app.services.farm_agent.prompts import (
    DIAGNOSIS_PROMPT,
    FARM_DATA_PROMPT,
    ORCHESTRATOR_PROMPT,
    SUBSIDY_PROMPT,
    VERIFIER_PROMPT,
)
from app.services.farm_agent.tools import (
    DIAGNOSIS_TOOLS,
    FARM_DATA_TOOLS,
    ORCHESTRATOR_TOOLS,
    SUBSIDY_TOOLS,
)

logger = logging.getLogger(__name__)

# 도메인 지식 메모리 — backend/memory/AGENTS.md 자동 로드.
# 추가 메모리 파일은 settings.FARM_AGENT_MEMORY_PATHS (CSV)로 확장 가능.
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_MEMORY_FILE = _BACKEND_ROOT / "memory" / "AGENTS.md"


def _build_llm() -> ChatOpenAI:
    """Gemma 4-31B-IT via LiteLLM proxy.

    Gemma는 reasoning 파라미터를 지원하지 않으므로 model_kwargs 비움.
    temperature=0.0 — 도구 라우팅 결정성.
    """
    return ChatOpenAI(
        base_url=settings.LITELLM_URL,
        api_key=settings.LITELLM_API_KEY,
        model=settings.LITELLM_MODEL,
        temperature=0.0,
        max_tokens=2048,
    )


def _build_memory_middleware() -> list[Any]:
    """MemoryMiddleware 구성 — AGENTS.md가 존재하면 1개, 없으면 빈 리스트.

    Deep Agents v0.5의 MemoryMiddleware는 시스템 프롬프트에 메모리 내용을
    주입해 모든 LLM 호출에 자동 포함시킨다. 에이전트가 새로운 학습 내용을
    edit_file로 본 파일에 추가할 수도 있다 (자기 개선).
    """
    if not _DEFAULT_MEMORY_FILE.exists():
        return []
    try:
        from deepagents import MemoryMiddleware
        from deepagents.backends.filesystem import FilesystemBackend

        backend = FilesystemBackend(root_dir=str(_BACKEND_ROOT))
        relative = _DEFAULT_MEMORY_FILE.relative_to(_BACKEND_ROOT)
        return [MemoryMiddleware(backend=backend, sources=[f"./{relative.as_posix()}"])]
    except Exception as exc:  # noqa: BLE001 — 메모리 실패가 에이전트 빌드를 막지 않음
        logger.warning("memory_middleware.init_failed err=%s", exc)
        return []


def build_farm_agent(
    checkpointer: BaseCheckpointSaver | None = None,
    mcp_tools: list[Any] | None = None,
):
    """Deep Agent 컴파일. lifespan에서 1회 호출 후 app.state에 저장.

    Args:
        checkpointer: 멀티턴 영속 메모리. None이면 thread별 메모리 없음.
        mcp_tools: 외부 MCP 서버에서 어댑터로 가져온 도구 목록.
            오케스트레이터 레벨에 주입한다 — Deep Agents의 알려진 제약상
            서브에이전트가 MCP 도구를 직접 호출하지 못하기 때문이다.

    Memory:
        backend/memory/AGENTS.md를 MemoryMiddleware로 자동 로드해 시스템 프롬프트에 주입.
        한국 농업 도메인 상수·응답 원칙·안전 가이드라인을 모든 호출에 적용.
    """
    model = _build_llm()
    memory_middleware = _build_memory_middleware()

    subagents = [
        {
            "name": "diagnosis-agent",
            "description": (
                "병해충·질병 진단과 방제·농약 추천을 담당. "
                "입력으로 pest(해충명), crop(작물), region(지역)이 모두 필요."
            ),
            "system_prompt": DIAGNOSIS_PROMPT,
            "tools": DIAGNOSIS_TOOLS,
        },
        {
            "name": "subsidy-agent",
            "description": (
                "공익직불금·정책자금 자격 매칭과 시행지침 질의응답. "
                "자격 판정·시행지침 검색·프로그램 상세 모두 처리."
            ),
            "system_prompt": SUBSIDY_PROMPT,
            "tools": SUBSIDY_TOOLS,
        },
        {
            "name": "farm-data-agent",
            "description": (
                "날씨·예보·KAMIS 시세·영농일지 조회·IoT 제어 이력 조회 (읽기 전용). "
                "실제 IoT 제어 명령은 처리하지 않음."
            ),
            "system_prompt": FARM_DATA_PROMPT,
            # MCP 도구도 함께 받아 farm-data-agent가 외부 데이터(예: 글로벌 날씨)도 활용 가능.
            # Deep Agents 제약으로 동일 객체를 양쪽에 등록하지만 부작용 없음(읽기 전용).
            "tools": FARM_DATA_TOOLS + list(mcp_tools or []),
        },
        {
            "name": "verifier-agent",
            "description": (
                "농약·희석배수·살포 시기 등 안전 검증을 수행. "
                "diagnosis-agent 결과를 사용자에게 보내기 전에 반드시 호출."
            ),
            "system_prompt": VERIFIER_PROMPT,
            # 검증을 위해 동일한 진단 도구를 재호출
            "tools": DIAGNOSIS_TOOLS,
        },
    ]

    top_level_tools = ORCHESTRATOR_TOOLS + list(mcp_tools or [])

    return create_deep_agent(
        model=model,
        tools=top_level_tools,
        system_prompt=ORCHESTRATOR_PROMPT,
        subagents=subagents,
        checkpointer=checkpointer,
        middleware=memory_middleware,  # AGENTS.md 자동 로드
    )
