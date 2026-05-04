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
    """Farm Agent 전용 LLM.

    Selection:
      - OPENROUTER_API_KEY 가 설정돼 있으면 OpenRouter (Grok 4.1 Fast) 사용.
        reasoning.enabled=true 를 extra_body 로 전송해 모델이 Chain-of-Thought
        를 생성하도록 한다. langchain_openai 의 ChatOpenAI 는 사내 model_kwargs
        를 통해 임의 OpenAI-호환 필드를 그대로 전달한다.
      - 미설정 시 기존 LiteLLM 프록시(Gemma 등)로 폴백.

    temperature=0.0 — 도구 라우팅 결정성.
    """
    if settings.OPENROUTER_API_KEY:
        # langchain-openai 의 버전 차이를 흡수: 일부 버전은 `extra_body=` 를
        # 최상위 인자로 받고, 일부는 `model_kwargs={"extra_body": {...}}` 로만
        # 받는다. 둘 다 시도해보고 동작하는 쪽으로 초기화.
        common_kwargs: dict[str, object] = {
            "base_url": settings.OPENROUTER_BASE_URL,
            "api_key": settings.OPENROUTER_API_KEY,
            "model": settings.OPENROUTER_MODEL,
            "temperature": 0.0,
            "max_tokens": 2048,
            # OpenRouter 가 원천 모델 식별을 위해 권장하는 헤더 — 일부 모델은
            # 헤더 없으면 거부할 수 있다.
            "default_headers": {
                "HTTP-Referer": "https://farmos.local",
                "X-Title": "FarmOS Agent",
            },
        }
        # 병렬 도구 호출 활성화 — 사용자 질문이 여러 주제를 묻는 경우
        # (예: 직불금 자격 + 처벌) LLM 이 한 라운드에 다중 tool_call 을 발행해
        # LangGraph 가 동시 실행. 직렬 호출 대비 N개 검색을 ~N배 빠르게 처리.
        # iter 27: `parallel_tool_calls` 는 ChatOpenAI 의 top-level 인자가 아니라
        # `model_kwargs` 안에 들어가야 한다 (langchain_openai 0.3+ 가 top-level
        # 입력 시 UserWarning 후 자동 이동). 직접 model_kwargs 에 넣어 경고 제거.
        model_kwargs: dict[str, object] = {"parallel_tool_calls": True}
        extra_body: dict[str, object] = {}
        if settings.OPENROUTER_REASONING_ENABLED:
            extra_body["reasoning"] = {"enabled": True}
        if extra_body:
            model_kwargs["extra_body"] = extra_body

        logger.info(
            "farm_agent.llm provider=openrouter model=%s reasoning=%s",
            settings.OPENROUTER_MODEL,
            settings.OPENROUTER_REASONING_ENABLED,
        )
        if settings.OPENROUTER_REASONING_ENABLED:
            logger.warning(
                "farm_agent.llm.reasoning_ON — 한 LLM 호출당 8-25s 추가. Deep Agent "
                "는 한 턴에 LLM 을 3-4 회 호출하므로 latency 가 누적된다. 어려운 "
                "질의에만 ON 하고, 평소에는 .env 의 OPENROUTER_REASONING_ENABLED=false 로 둘 것."
            )

        # 1차: extra_body 를 model_kwargs 안에 — 가장 호환성 좋은 경로.
        try:
            return ChatOpenAI(**common_kwargs, model_kwargs=model_kwargs)
        except Exception as exc:  # noqa: BLE001 — fall back to top-level extra_body
            logger.warning(
                "farm_agent.llm.openrouter_model_kwargs_failed err=%s — top-level extra_body 로 재시도",
                exc,
            )
            # Fallback path: older langchain_openai accepts extra_body / parallel_tool_calls
            # as top-level kwargs. Keep parallel_tool_calls here so the fallback
            # also enables the optimisation.
            return ChatOpenAI(
                **common_kwargs,
                parallel_tool_calls=True,  # type: ignore[arg-type]
                extra_body=extra_body or None,  # type: ignore[arg-type]
            )

    logger.info(
        "farm_agent.llm provider=litellm model=%s",
        settings.LITELLM_MODEL,
    )
    return ChatOpenAI(
        base_url=settings.LITELLM_URL,
        api_key=settings.LITELLM_API_KEY,
        model=settings.LITELLM_MODEL,
        temperature=0.0,
        max_tokens=2048,
    )


def _register_grok_harness_profile() -> bool:
    """Register a Grok-specific HarnessProfile for deepagents v0.5+.

    HarnessProfile is a declarative override that deepagents auto-applies when
    the model matches. Each provider/model has different prompt sensitivity:
      - Grok 4.1 Fast: short prompts, tolerates terse instructions, dislikes
        long enumerations (re-anchors on each turn).
      - Anthropic/OpenAI: handle long structured prompts well.

    For Grok we add a system_prompt_suffix that re-emphasizes "answer only the
    current question" (countering Grok's observed multi-turn anchoring) and
    exclude the SummarizationMiddleware (auto-summarization at long context
    can wipe critical citations from the subsidy agent's working state).

    Best-effort: deepagents < 0.5 has no profile API → silently skip.
    Returns True if registered successfully.
    """
    try:
        from deepagents import HarnessProfile, register_harness_profile  # type: ignore[attr-defined]
    except ImportError:
        logger.info(
            "harness_profile.skip — deepagents version pre-dates HarnessProfile API "
            "(install deepagents>=0.5 to enable Grok-specific tuning)"
        )
        return False

    try:
        register_harness_profile(
            settings.OPENROUTER_MODEL,
            HarnessProfile(
                system_prompt_suffix=(
                    "\n\n## Grok 응답 가이드\n"
                    "- 현재 사용자 질문에만 답합니다. 이전 턴의 답변을 paraphrase하지 마세요.\n"
                    "- 도구 결과는 그대로 인용. 자체 해석으로 보강 금지.\n"
                    "- 동일한 응답을 두 번 출력 절대 금지."
                ),
                # SummarizationMiddleware 가 인용 태그 [doc > 제N조] 를 잘라낼 수 있음.
                # AnthropicPromptCachingMiddleware 는 Anthropic API 전용 — OpenRouter
                # 경유 Grok 호출엔 효과 없이 직렬화 비용만 발생 (per-call 0.5-2s).
                excluded_middleware={
                    "SummarizationMiddleware",
                    "AnthropicPromptCachingMiddleware",
                },
            ),
        )
        logger.info(
            "harness_profile.registered model=%s",
            settings.OPENROUTER_MODEL,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — profile 실패가 빌드 막지 않음
        logger.warning("harness_profile.register_failed err=%s", exc)
        return False


def _resolve_memory_sources() -> list[str]:
    """Resolve memory sources: AGENTS.md + settings.FARM_AGENT_MEMORY_PATHS (CSV).

    Returns POSIX-style relative paths anchored at backend/ for FilesystemBackend.
    Skips entries that don't exist on disk (warns once per missing file) so a
    misconfigured CSV never crashes agent build.

    The extra-paths slot is the wiring point for ReasoningBank-style
    STRATEGIES.md (strategy-level reasoning hints distilled from past traces),
    POLICIES.md (operator-curated guardrails), etc. — multiple memory files
    layered into every LLM call without changing call sites.
    """
    sources: list[str] = []
    if _DEFAULT_MEMORY_FILE.exists():
        sources.append(f"./{_DEFAULT_MEMORY_FILE.relative_to(_BACKEND_ROOT).as_posix()}")

    extra_csv = (settings.FARM_AGENT_MEMORY_PATHS or "").strip()
    if not extra_csv:
        return sources

    for raw in extra_csv.split(","):
        candidate = raw.strip()
        if not candidate:
            continue
        path = Path(candidate)
        if not path.is_absolute():
            path = _BACKEND_ROOT / path
        if not path.exists():
            logger.warning("memory_middleware.source_missing path=%s — skipped", path)
            continue
        try:
            rel = path.resolve().relative_to(_BACKEND_ROOT)
        except ValueError:
            logger.warning(
                "memory_middleware.source_outside_backend path=%s — skipped (must live under backend/)",
                path,
            )
            continue
        sources.append(f"./{rel.as_posix()}")
    return sources


def _build_memory_middleware() -> list[Any]:
    """MemoryMiddleware 구성 — AGENTS.md + 옵션 추가 메모리 파일.

    Deep Agents v0.5의 MemoryMiddleware는 시스템 프롬프트에 메모리 내용을
    주입해 모든 LLM 호출에 자동 포함시킨다. 에이전트가 새로운 학습 내용을
    edit_file로 본 파일에 추가할 수도 있다 (자기 개선).

    추가 파일은 settings.FARM_AGENT_MEMORY_PATHS (CSV) 로 지정한다.
    ReasoningBank 패턴 (Google 2025) 의 strategy-level memory 적용을 위해
    기본값으로 memory/STRATEGIES.md 를 포함한다.
    """
    sources = _resolve_memory_sources()
    if not sources:
        return []
    try:
        from deepagents import MemoryMiddleware
        from deepagents.backends.filesystem import FilesystemBackend

        backend = FilesystemBackend(root_dir=str(_BACKEND_ROOT))
        logger.info("memory_middleware.sources count=%d files=%s", len(sources), sources)
        return [MemoryMiddleware(backend=backend, sources=sources)]
    except Exception as exc:  # noqa: BLE001 — 메모리 실패가 에이전트 빌드를 막지 않음
        logger.warning("memory_middleware.init_failed err=%s", exc)
        return []


def _try_async_verifier_subagent() -> Any | None:
    """Build the verifier as an async subagent if deepagents v0.5+ is available.

    Async pattern (Deep Agents v0.5):
      - Verifier runs in background after diagnose-agent completes.
      - Main flow returns the diagnosis answer to user immediately.
      - Verifier's task_id is logged; failures emit a warning event.

    Why: the verifier reads the diagnosis output and re-runs `diagnose_pest`
    (~3-8s) to compare. Synchronously blocking the user behind that step gives
    no UX value — `diagnose_pest` already has a deterministic safety gate.
    Async lets us keep the cross-check for audit logs without slowing the user.

    Returns None if deepagents is too old to support AsyncSubAgent — caller falls
    back to the synchronous SubAgent dict.
    """
    try:
        from deepagents import AsyncSubAgent  # type: ignore[attr-defined]
    except ImportError:
        return None

    try:
        return AsyncSubAgent(
            name="verifier-agent",
            description=(
                "농약·희석배수·살포 시기 안전 검증 (백그라운드 실행). "
                "diagnose 결과 후 비동기로 cross-check — 사용자 응답 차단하지 않음."
            ),
            # graph_id must match a graph registered in langgraph.json (or
            # programmatically via langgraph SDK). We use the same name as the
            # subagent for convention; deployment-side registration is documented
            # in backend/langgraph.json (create separately if not present).
            graph_id="verifier-agent",
            # url omitted → ASGI in-process transport, no network hop.
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("async_subagent.verifier_build_failed err=%s — falling back to sync", exc)
        return None


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

    Harness Profile:
        Grok 4.1 Fast 용 declarative override (deepagents>=0.5) 가 자동 등록됨.
        Grok 의 multi-turn anchoring · long-prompt 약점을 보정.

    Async Subagents:
        verifier-agent 는 가능하면 AsyncSubAgent (백그라운드 실행) 으로 등록.
        deepagents<0.5 환경에서는 sync SubAgent 로 fallback.
    """
    # Grok-specific harness profile (best-effort, no-op on older deepagents).
    if settings.OPENROUTER_API_KEY:
        _register_grok_harness_profile()

    model = _build_llm()
    memory_middleware = _build_memory_middleware()

    # Verifier as async subagent if supported, else sync dict (existing behavior).
    async_verifier = _try_async_verifier_subagent()
    sync_verifier = {
        "name": "verifier-agent",
        "description": (
            "농약·희석배수·살포 시기 등 안전 검증을 수행. "
            "diagnosis-agent 결과를 사용자에게 보내기 전에 반드시 호출."
        ),
        "system_prompt": VERIFIER_PROMPT,
        # 검증을 위해 동일한 진단 도구를 재호출
        "tools": DIAGNOSIS_TOOLS,
    }
    verifier_subagent = async_verifier or sync_verifier
    if async_verifier is not None:
        logger.info("farm_agent.verifier mode=async (background ASGI)")
    else:
        logger.info("farm_agent.verifier mode=sync (blocking)")

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
        verifier_subagent,
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
