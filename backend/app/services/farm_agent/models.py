"""Tiered LLM routing for Farm Agent.

각 에이전트(orchestrator, diagnosis, subsidy, farm_data, verifier, briefing)에
서로 다른 모델을 라우팅. settings.FARM_AGENT_PROFILE 로 프로파일 선택,
settings.FARM_AGENT_MODEL_<ROLE> 로 개별 오버라이드 가능.

라우팅 결정 로직:
  1. settings.FARM_AGENT_MODEL_<ROLE> 가 비어있지 않으면 그 값 사용 (override)
  2. PROFILE 에서 role 의 기본 모델 사용
  3. 그래도 없으면 기존 OPENROUTER_MODEL / LITELLM_MODEL 폴백 (호환성)

모델 슬러그가 슬래시(/) 를 포함하면 OpenRouter 호출, 아니면 LiteLLM 호출.
"""

from __future__ import annotations

import logging
from typing import Literal

from langchain_openai import ChatOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


Role = Literal[
    "orchestrator",
    "diagnosis",
    "subsidy",
    "subsidy_escalation",
    "farm_data",
    "verifier",
    "briefing",
]


# ── Profile presets ──────────────────────────────────────────────────────────
# Slug 형식: "<provider>/<model>" → OpenRouter, 슬래시 없으면 LiteLLM 모델 ID.
# May 2026 OpenRouter 확인된 모델만 사용.

_PROFILES: dict[str, dict[Role, str]] = {
    "balanced": {
        "orchestrator":        "google/gemini-2.5-flash",
        "diagnosis":           "anthropic/claude-haiku-4.5",
        "subsidy":             "anthropic/claude-sonnet-4.6",
        "subsidy_escalation":  "anthropic/claude-opus-4.7",
        "farm_data":           "anthropic/claude-haiku-4.5",
        "verifier":            "deepseek/deepseek-v4-flash",
        "briefing":            "anthropic/claude-sonnet-4.6",
    },
    "premium": {
        "orchestrator":        "anthropic/claude-haiku-4.5",
        "diagnosis":           "anthropic/claude-sonnet-4.6",
        "subsidy":             "anthropic/claude-opus-4.7",
        "subsidy_escalation":  "anthropic/claude-opus-4.7",
        "farm_data":           "anthropic/claude-sonnet-4.6",
        "verifier":            "anthropic/claude-haiku-4.5",
        "briefing":            "anthropic/claude-opus-4.7",
    },
    "budget": {
        "orchestrator":        "google/gemini-2.5-flash",
        "diagnosis":           "deepseek/deepseek-v4-flash",
        "subsidy":             "deepseek/deepseek-v4-pro",
        "subsidy_escalation":  "deepseek/deepseek-v4-pro",
        "farm_data":           "deepseek/deepseek-v4-flash",
        "verifier":            "deepseek/deepseek-v4-flash",
        "briefing":            "google/gemini-2.5-flash",
    },
}


def resolve_model(role: Role) -> str:
    """역할에 해당하는 모델 슬러그 반환.

    우선순위: 개별 ENV override → PROFILE 매핑 → 기존 단일 모델 폴백.
    """
    override_attr = f"FARM_AGENT_MODEL_{role.upper()}"
    override = getattr(settings, override_attr, "") or ""
    if override:
        return override

    profile_name = (settings.FARM_AGENT_PROFILE or "balanced").lower()
    profile = _PROFILES.get(profile_name)
    if profile and role in profile:
        return profile[role]

    # 폴백: 기존 단일 모델 동작 유지
    if settings.OPENROUTER_API_KEY and settings.OPENROUTER_MODEL:
        return settings.OPENROUTER_MODEL
    return settings.LITELLM_MODEL


def build_llm_for(role: Role, *, max_tokens: int = 2048, temperature: float = 0.0) -> ChatOpenAI:
    """역할별 ChatOpenAI 인스턴스 생성.

    슬래시 포함 슬러그 → OpenRouter, 아니면 LiteLLM. OpenRouter API key 가 없으면
    슬러그 형식과 무관하게 LiteLLM 으로 폴백 (개발 환경에서 OpenRouter 미설정 시).
    """
    model_slug = resolve_model(role)
    use_openrouter = "/" in model_slug and bool(settings.OPENROUTER_API_KEY)

    if use_openrouter:
        common_kwargs: dict[str, object] = {
            "base_url": settings.OPENROUTER_BASE_URL,
            "api_key": settings.OPENROUTER_API_KEY,
            "model": model_slug,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "default_headers": {
                "HTTP-Referer": "https://farmos.local",
                "X-Title": f"FarmOS Agent ({role})",
            },
        }
        # Per-role reasoning effort. OpenRouter 가 effort 값을 모든 프로바이더로 정규화
        # (Anthropic max_tokens, OpenAI/Gemini thinkingLevel, DeepSeek effort) — 코드 한
        # 군데에서 다 다룰 수 있음. 단순 라우팅·검증·요약 역할은 reasoning 불필요
        # (지연만 추가). 고난도 (escalation·briefing 합성) 만 high effort.
        # Reasoning effort per role. The global OPENROUTER_REASONING_ENABLED flag
        # gates ALL reasoning to prevent surprise latency (8-25s/call) on
        # operator-disabled environments. subsidy_escalation defaults to "high"
        # when the flag is on (UNCLEAR re-examination justifies the cost), and
        # falls back to "medium" when disabled — the escalation still happens
        # via the Sonnet→Opus model swap, just without explicit thinking budget.
        reasoning_on = settings.OPENROUTER_REASONING_ENABLED
        reasoning_effort_by_role: dict[str, str | None] = {
            "orchestrator":         None,
            "diagnosis":            None,
            "farm_data":            None,
            "verifier":             None,
            "subsidy":              "medium" if reasoning_on else None,
            "subsidy_escalation":   "high"   if reasoning_on else None,
            "briefing":             "medium" if reasoning_on else None,
        }
        effort = reasoning_effort_by_role.get(role)
        extra_body: dict[str, object] | None = (
            {"reasoning": {"effort": effort}} if effort is not None else None
        )

        logger.info(
            "farm_agent.llm role=%s provider=openrouter model=%s reasoning=%s",
            role, model_slug, effort or "off",
        )
        # langchain_openai 0.3+: extra_body 와 parallel_tool_calls 모두 top-level kwarg.
        # 구버전 폴백 분기는 제거 — 현재 의존성 (uv.lock) 이 0.3+ 으로 핀(pin) 되어 있음.
        return ChatOpenAI(
            **common_kwargs,
            parallel_tool_calls=True,
            extra_body=extra_body,
        )

    # LiteLLM 경로 (또는 슬러그가 OpenRouter 슬래시 형식이지만 키 미설정)
    litellm_model = model_slug if "/" not in model_slug else settings.LITELLM_MODEL
    logger.info("farm_agent.llm role=%s provider=litellm model=%s", role, litellm_model)
    return ChatOpenAI(
        base_url=settings.LITELLM_URL,
        api_key=settings.LITELLM_API_KEY,
        model=litellm_model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
