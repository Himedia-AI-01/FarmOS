"""Farm Agent 대화 API.

POST /farm-agent/ask     — 일회성 응답 (JSON, 비스트림 클라이언트용)
POST /farm-agent/stream  — SSE 토큰 스트림 (실시간 채팅 UI용, agentic events)
GET  /farm-agent/threads — 사용자 대화 스레드 목록
GET  /farm-agent/threads/{session_id} — 단일 스레드 메시지 복원
POST /farm-agent/approve-action — HITL: 에이전트가 제안한 IoT 제어 실행

세션 영속성:
  - thread_id = `f"{user_id}:{session_id}"`
  - AsyncPostgresSaver가 lifespan에서 주입한 checkpointer로 thread별 상태 저장
"""

from __future__ import annotations

import io
import json as _json
import logging
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field
from PIL import Image
from sse_starlette.sse import EventSourceResponse

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.stt import transcribe_audio
from app.models.user import User
from app.services.farm_agent import answer_cache
from app.services.farm_agent.briefing import get_or_generate_briefing
from app.services.farm_agent.reasoning_bank import (
    distill_strategies,
    record_async as record_trajectory_async,
)
from app.services.farm_agent.fast_path import try_fast_path
from app.services.farm_agent import langcache
from app.services.farm_agent.verifier_candidates import record_verifier_verdict
from app.services.pest_classifier import (
    ClassifierError,
    ConfigurationError as ClassifierConfigError,
    classify_pest_image,
)

# 업로드 한도
_MAX_IMAGE_BYTES = 10 * 1024 * 1024   # 10 MB
_MAX_AUDIO_BYTES = 25 * 1024 * 1024   # 25 MB
_CHUNK_BYTES = 1024 * 1024            # 1 MB chunks for streaming reads
_DIAGNOSIS_UPLOAD_DIR = Path(settings.UPLOAD_BASE_DIR) / "diagnosis"
_DIAGNOSIS_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


async def _read_with_size_cap(file: UploadFile, max_bytes: int) -> bytes:
    """청크 단위로 읽으며 크기 한도를 감시 — OOM 방어.

    과거 `await file.read()` 한 번에 전체를 메모리에 올린 뒤 크기를 검사하던 패턴은
    악의적인 5GB 업로드가 서버를 OOM 시킬 수 있었다. 한 청크라도 한도를 넘기면
    즉시 413 으로 반환해 메모리 폭주를 차단한다.
    """
    buf = bytearray()
    while True:
        chunk = await file.read(_CHUNK_BYTES)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"파일 크기가 한도({max_bytes // (1024*1024)}MB)를 초과합니다.",
            )
    return bytes(buf)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/farm-agent", tags=["farm-agent"])


class AskIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = Field(
        default=None,
        description="대화 세션 식별자. None이면 신규 세션 발급.",
    )


class AskOut(BaseModel):
    answer: str
    session_id: str
    fast_path: bool = Field(
        default=False,
        description="True면 LLM 오케스트레이터를 건너뛰고 도구를 직접 호출한 응답.",
    )


def _agent(request: Request):
    agent = getattr(request.app.state, "farm_agent", None)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Farm Agent가 초기화되지 않았습니다. 서버 재시작이 필요할 수 있습니다.",
        )
    return agent


def _runtime_config(user_id: str, session_id: str) -> dict[str, Any]:
    """RunnableConfig — checkpointer thread + 도구 런타임 의존성 주입.

    `recursion_limit` raised above LangGraph's default of 25 — the orchestrator
    plans across 4 sub-agents and can chain 30+ steps on subsidy + diagnosis
    follow-ups. Sub-agents inherit their own (higher) limit via the patched
    deepagents middleware in `services.farm_agent.agent`.
    """
    return {
        "configurable": {
            "thread_id": f"{user_id}:{session_id}",
            "user_id": user_id,
        },
        "recursion_limit": int(settings.FARM_AGENT_RECURSION_LIMIT),
    }


def _content_to_text(content: Any) -> str:
    """Normalize LangChain/OpenAI content shapes into displayable text.

    Chat chunks are usually strings, but tool-heavy Deep Agent paths can store
    final content as provider-specific block lists. The frontend only wants
    human-readable assistant text, not tool-call metadata.

    Reasoning-model safety: Grok 4.1 Fast (and similar) with reasoning enabled
    returns reasoning as a separate content block (type "thinking" / "reasoning"
    / "reasoning_content"). If we accept those alongside the final "text" block,
    the user-visible bubble shows both the chain-of-thought AND the answer —
    which looks like a duplicated/repeated response. We explicitly skip those
    blocks; reasoning belongs in logs / debug UI, not the chat bubble.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            block_type = item.get("type")
            # Drop reasoning/thinking blocks — they aren't user-facing and
            # cause "the agent repeats itself" when concatenated with the answer.
            if block_type in {"thinking", "reasoning", "reasoning_content"}:
                continue
            if block_type in {None, "text", "output_text"}:
                text = item.get("text") or item.get("content") or item.get("value")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


# Subagent-delegation tool names (Deep Agents). Their ToolMessage content IS
# the user-visible answer when the orchestrator's final AIMessage is empty —
# Gemma often skips synthesis and just lets the tool result speak.
_SUBAGENT_DELEGATION_TOOL_NAMES = {"task"}
_PLAN_TOOL_NAMES = {"write_todos"}


# ── Direct routing (Fix #4) ─────────────────────────────────────────────────
#
# Skip the orchestrator's routing-decision LLM call when keywords unambiguously
# point to a single subagent. We do this by prepending a routing directive to
# the user message so the orchestrator stops deliberating and immediately calls
# `task(<subagent>)`. Combined with skip-synthesis (Fix #1), the orchestrator
# does effectively zero LLM work for these flows — total ~3-5s saved per turn.
#
# Conservative on purpose: ambiguous queries (mixed domains, very short, no
# clear keyword) fall through to the normal LLM-driven routing.

import re as _re

_ROUTING_KEYWORDS = {
    "subsidy-agent": (
        "직불금", "직불", "공익직불", "소농직불", "면적직불", "지원금",
        "정책자금", "시행지침", "준수사항", "감액", "부정수급",
        "기본직불", "농업인 자격", "청년농", "가산금",
        # STT 발음 변이 (Whisper 한국어 농업 도메인 변이) — AGENTS.md 기재 패턴
        "직물금", "직불검",
    ),
    "diagnosis-agent": (
        "병해충", "노균병", "흰가루병", "탄저병", "응애", "진딧물", "도열병",
        "잿빛곰팡이", "잎곰팡이", "갈반병", "녹병", "검은별무늬", "역병",
        "무름병",
        "방제", "약제 추천", "농약 추천", "잎이 노래", "잎이 누렇",
        "흰 가루", "거미줄", "끈적한", "물러졌",
        # STT 발음 변이
        "노근병", "노깐병", "옆면살포", "히석",
    ),
    "farm-data-agent": (
        "영농일지", "영농기록", "어제 일지", "오늘 일지", "이번 주 작업",
        "최근 환기", "최근 관수", "최근 차광", "최근 IoT",
        "일주일 일지", "일지 요약",
    ),
}

# Multi-domain hints — 명확한 다중 도메인 접속어만. 한국어 일반 조사 (랑/이랑/와/과)
# 는 일반 명사 연결에도 흔히 등장하므로 fast-path 를 비활성화시키지 않는다.
# (예전: "사과**랑** 배 시세" 같은 단순 질의도 fast-path 우회되어 비효율)
_MULTI_DOMAIN_HINTS = ("그리고", "또한", "더해서", "추가로", "함께")


_OBLIGATION_PHRASES = (
    "꼭 해야", "꼭 써야", "꼭 받아야", "해야 하나", "써야 해",
    "안 해도 되", "안 써도 되", "안 받아도 되", "안 들어도 되",
    "안 가도 되", "안 가도 돼", "안 나가도", "더 줘도 되",
    "그냥 태워도", "그냥 모아", "그냥 버려도",
    "의무", "준수", "감액", "필수", "의무사항",
    "버려야", "보관해야", "신고해야",
)
_IOT_LOOKUP_PHRASES = (
    "IoT 제어", "IoT 자율", "자율 제어", "자율제어",
    "어제 IoT", "최근 IoT", "IoT 이력",
)


def _detect_single_domain(question: str) -> str | None:
    """Return the unambiguous subagent name for this question, or None.

    Decision order (most-specific wins):
      1. Obligation phrasing ("꼭 해야", "의무사항", "감액") → subsidy-agent
         even if other domain keywords appear ("농약 의무사항" → subsidy, not diagnosis).
      2. IoT lookup phrasing ("IoT 자율 제어 이력") → farm-data-agent.
      3. Single keyword match in _ROUTING_KEYWORDS → that agent.
      4. Multiple domain keywords or multi-domain conjunction → None (orchestrator).
      5. No keyword match → None (orchestrator).
    """
    if not question or len(question) > 200:
        return None
    if any(hint in question for hint in _MULTI_DOMAIN_HINTS):
        return None

    # Tier-1: obligation phrases route to subsidy. Exception: when a competing
    # *diagnosis* keyword is also present, the question crosses two domains
    # (e.g. "오늘 진딧물 약 사고 일지도 써야 해") → orchestrator coordinates.
    # Farm-data overlap (영농기록/일지/관수 etc.) is NOT an exception because
    # those topics align with subsidy obligations (8대 ⑧ 영농기록, ② 농지 형상,
    # etc.) — the subsidy subagent answers correctly with citations.
    if any(phrase in question for phrase in _OBLIGATION_PHRASES):
        diag_hits = any(kw in question for kw in _ROUTING_KEYWORDS["diagnosis-agent"])
        if diag_hits:
            return None
        return "subsidy-agent"

    # Tier-2: IoT lookup specific phrasing routes to farm-data even though
    # neither "IoT" nor "어제" alone is in the keyword table.
    if any(phrase in question for phrase in _IOT_LOOKUP_PHRASES):
        return "farm-data-agent"

    matches: list[str] = []
    for agent, keywords in _ROUTING_KEYWORDS.items():
        if any(kw in question for kw in keywords):
            matches.append(agent)
    if len(matches) == 1:
        return matches[0]
    return None


def _wrap_with_routing_hint(question: str) -> str:
    """If the question clearly maps to one subagent, prepend a routing directive.

    The orchestrator prompt instructs it to obey explicit routing hints without
    re-analyzing. This effectively bypasses the orchestrator's routing LLM call
    (~2-5s saved on Grok 4.1 Fast).
    """
    target = _detect_single_domain(question)
    if not target:
        return question
    logger.info("farm_agent.direct_routing target=%s", target)
    return (
        f"[ROUTING_HINT] 이 질문은 명확히 `{target}` 도메인입니다. "
        f"라우팅 분석 없이 즉시 `task({target})` 도구를 호출하세요.\n\n"
        f"사용자 질문: {question}"
    )

# IoT-control intent recognized by the agent's free-form reply. When detected
# we emit an `action` SSE event so the frontend can render an HITL approval card
# instead of just dumping plain text. Read-only by default — actual relay POST
# only fires after explicit user confirmation via /approve-action.
_IOT_ACTION_KEYWORDS = {
    "ventilation": ("환기", "창 열", "창문", "팬", "fan"),
    "irrigation": ("관수", "물 주", "급수", "irrigat"),
    "lighting": ("조명", "라이트", "light"),
    "shading": ("차광", "보온", "커튼", "shade"),
}


def _looks_like_iot_action_proposal(text: str) -> dict[str, Any] | None:
    """Heuristic: does the agent's reply propose a concrete IoT action?

    Returns a structured proposal {control_type, summary} when the reply
    contains both a recognized control keyword and an actionable verb
    ("권장", "필요", "켜", "끄", "여시", "닫으"). The frontend renders this
    as an approval card with an [실행] button. Conservative on purpose —
    the agent prompt forbids issuing direct commands, so this only triggers
    on advisory language.
    """
    if not text or len(text) > 4000:
        return None
    actionable = any(verb in text for verb in ("권장", "필요", "여시", "닫으", "켜시", "끄시", "가동"))
    if not actionable:
        return None
    for control_type, keywords in _IOT_ACTION_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return {"control_type": control_type}
    return None


def _extract_citations(text: str) -> list[dict[str, str]]:
    """Pull subsidy-style citations from agent answers.

    Recognises the citation shapes the 시행지침 RAG pipeline actually emits:

    1. `[doc > 제N조 ...]`              — 법령 article style (legacy)
    2. `[..., CHAPTER N ...]`           — chapter-tagged chunks (current indexer)
    3. `[I./II./VI. heading, ...]`      — Roman-numeral 시행지침 sections
    4. `[N. heading, CHAPTER M > II.]`  — numbered top-level sections

    Returns a list of {label, doc, snippet} (snippet may be empty — the UI
    surfaces label as a chip). Best-effort: malformed citations are skipped.
    """
    import re as _re
    citations: list[dict[str, str]] = []
    seen: set[str] = set()
    # Pattern union — each branch matches the *interior* of the brackets so
    # downstream label parsing (`split(">", 1)`) keeps working unchanged.
    pattern = _re.compile(
        r"\[("
        r"[^\[\]]+?>\s*제\s*\d+\s*조[^\[\]]*?"      # 제N조 article style
        r"|"
        r"[^\[\]]*?CHAPTER\s*\d+[^\[\]]*?"           # CHAPTER N anywhere
        r"|"
        r"[IVX]+\.\s+[^\[\]]+?"                       # leading Roman-numeral section
        r"|"
        r"\d+\.\s+[^\[\]]*?(?:CHAPTER\s*\d+|>)[^\[\]]*?"  # "6. 준수사항 ..." with CHAPTER/>
        r")\]"
    )
    for match in pattern.finditer(text):
        label = match.group(1).strip()
        if label in seen:
            continue
        seen.add(label)
        # Split on the first ">" (article style) or "," (chapter style) to get
        # a doc-ish prefix; otherwise the whole label doubles as snippet.
        if ">" in label:
            doc, clause = label.split(">", 1)
        elif "," in label:
            doc, clause = label.split(",", 1)
        else:
            doc, clause = label, ""
        citations.append(
            {"label": label, "doc": doc.strip(), "snippet": clause.strip()}
        )
    return citations


# Content-side keywords used to recognise subsidy-domain *answers* even when
# the user question itself didn't carry a routing keyword (e.g. multi-topic
# questions where the orchestrator decided subsidy was relevant). Pattern
# overlap with `_ROUTING_KEYWORDS["subsidy-agent"]` is intentional.
_SUBSIDY_ANSWER_HINTS = (
    "직불", "공익직불", "기본직불", "소농직불", "면적직불", "준수사항",
    "시행지침", "감액", "부정수급", "농업인 자격",
)


# Meta / capability questions about the agent itself. Answers to these list
# multiple domains (including 직불금) by design — without this exemption the
# citation guardrail false-fires on a perfectly correct help response.
_META_QUESTION_HINTS = (
    "기능", "뭐 할 수 있", "뭘 할 수 있", "뭐가 있", "뭐가있",
    "도움말", "사용법", "넌 누구", "너 누구", "당신은 누구",
    "어떤 일", "어떤 걸", "어떻게 사용", "what can you", "help",
)


def _is_meta_question(question: str) -> bool:
    """True if the question is about the agent's own capabilities/identity."""
    q = (question or "").lower()
    return any(hint in q for hint in _META_QUESTION_HINTS)


def _is_subsidy_domain_answer(question: str, answer: str) -> bool:
    """True if the QUESTION is subsidy-domain (citation guard scope tightened).

    Previously this also returned True if the answer mentioned subsidy keywords —
    but a market/weather/diagnosis question whose answer happens to mention
    "직불" tangentially shouldn't trigger 시행지침 citation requirement. The guard
    must scope to questions where 시행지침 citation is genuinely expected.

    Rule: question contains an explicit subsidy keyword AND no other domain
    keyword. Mixed questions (e.g. "직불금 받으면서 농약은 어떻게?") still slip
    through — that's intentional, those answers do need citation.
    """
    if _is_meta_question(question or ""):
        return False
    q = question or ""
    if not any(kw in q for kw in _SUBSIDY_ANSWER_HINTS):
        return False
    # Question explicitly mentions subsidy — guard applies.
    return True


# Directive injected into a follow-up turn when the citation guardrail fires.
# Kept terse (Grok 4.1 Fast prefers short steers) and explicitly references the
# tool the subsidy-agent should use, so the orchestrator doesn't waste a round
# re-deciding what to call.
_CITATION_REPROMPT_DIRECTIVE = (
    "[CITATION_GUARD] 직전 답변에 시행지침 인용이 없어 재작성이 필요합니다. "
    "`search_subsidy_regulations` (또는 `search_subsidy_regulations_fast`) 으로 "
    "근거 조항을 검색한 뒤, 답변 본문에 반드시 `[공익직불 시행지침 > 제N조]` "
    "형식의 인용 태그를 1개 이상 포함하세요. 인용 없는 답변은 거부됩니다.\n\n"
    "원래 질문: "
)


async def _maybe_reprompt_for_citation(
    agent: Any,
    config: dict[str, Any],
    *,
    question: str,
    answer: str,
    user_id: str,
    session_id: str,
) -> str:
    """If the guardrail triggers, run one bounded follow-up turn forcing a citation.

    Bounded by `FARM_AGENT_CITATION_REPROMPT_MAX` (default 1). Re-uses the same
    checkpointed thread so the agent retains the prior context — only one extra
    LLM round-trip per failure case. Returns the new answer if the retry
    produced citations, else returns the original.

    Never raises: any failure is logged and the original answer is preserved.
    """
    if not settings.FARM_AGENT_CITATION_REPROMPT_ENABLED:
        return answer
    if _extract_citations(answer):
        return answer
    if not _is_subsidy_domain_answer(question, answer):
        return answer

    max_retries = max(0, int(getattr(settings, "FARM_AGENT_CITATION_REPROMPT_MAX", 1)))
    if max_retries < 1:
        return answer

    directive = _CITATION_REPROMPT_DIRECTIVE + question
    current = answer
    for attempt in range(1, max_retries + 1):
        try:
            state = await agent.ainvoke(
                {"messages": [{"role": "user", "content": directive}]},
                config=config,
            )
        except Exception:  # noqa: BLE001 — never break the user response
            logger.exception(
                "farm_agent.citation_reprompt_failed user=%s session=%s attempt=%d",
                user_id, session_id, attempt,
            )
            return current
        retried = _latest_assistant_text_from_state(state)
        if retried and _extract_citations(retried):
            logger.info(
                "farm_agent.citation_reprompt.success user=%s session=%s attempt=%d",
                user_id, session_id, attempt,
            )
            return retried
        if retried:
            current = retried  # keep latest even if still uncited
    logger.warning(
        "farm_agent.citation_reprompt.exhausted user=%s session=%s attempts=%d",
        user_id, session_id, max_retries,
    )
    return current


def _is_subagent_delegation_tool_message(message: Any) -> bool:
    """True if message is a ToolMessage whose source is a subagent-delegation tool."""
    message_type = getattr(message, "type", None)
    role = getattr(message, "role", None)
    if message_type != "tool" and role != "tool":
        return False
    name = getattr(message, "name", None) or ""
    return name in _SUBAGENT_DELEGATION_TOOL_NAMES


def _is_human_message(message: Any) -> bool:
    return (
        getattr(message, "type", None) == "human"
        or getattr(message, "role", None) == "user"
    )


def _current_turn_messages(messages: list[Any]) -> list[Any]:
    """Return only the messages produced after the most recent HumanMessage.

    Multi-turn checkpointed state contains every prior turn. Recovery must
    look only at the *current* turn's messages — otherwise an empty AIMessage
    in turn N causes the walker to scan back into turn N-1 and reuse its
    answer, making the previous response appear again.
    """
    for idx in range(len(messages) - 1, -1, -1):
        if _is_human_message(messages[idx]):
            return messages[idx + 1:]
    return list(messages)


def _latest_assistant_text_from_state(state: Any) -> str:
    """Return the current turn's assistant text from a graph state.

    Preference order (within the current turn only):
      1. Most recent AIMessage with non-empty content (normal path).
      2. Most recent `task` ToolMessage content (Deep Agents fallback —
         when the orchestrator delegated and produced no final synthesis).

    Handles two state shapes (LangGraph API returns either depending on caller):
      - `agent.ainvoke(...)` → raw dict with "messages" key directly.
      - `agent.aget_state(config)` → StateSnapshot with `.values` attribute.

    Critical: `getattr(dict, "values", dict)` returns the dict's built-in
    `.values()` bound method, NOT the messages — so we must isinstance-check
    the state itself before falling through to the StateSnapshot path.
    """
    if state is None:
        return ""
    if isinstance(state, dict):
        values: dict = state
    else:
        values_attr = getattr(state, "values", None)
        if not isinstance(values_attr, dict):
            return ""
        values = values_attr
    all_messages = values.get("messages", [])
    turn_messages = _current_turn_messages(all_messages)

    delegation_fallback = ""
    tool_text_fallback = ""
    for message in reversed(turn_messages):
        if _is_subagent_delegation_tool_message(message):
            text = _content_to_text(getattr(message, "content", None)).strip()
            if text and not delegation_fallback:
                delegation_fallback = text
            continue
        message_type = getattr(message, "type", None)
        role = getattr(message, "role", None)
        if message_type in {"human", "system"} or role in {"user", "system"}:
            continue
        if message_type == "tool" or role == "tool":
            # Non-delegation tool result — keep as last-resort fallback. Better
            # to surface a tool's raw answer than to show "" to the user when
            # Gemma drops the orchestrator synthesis step entirely.
            text = _content_to_text(getattr(message, "content", None)).strip()
            if text and not tool_text_fallback:
                tool_text_fallback = text
            continue
        text = _content_to_text(getattr(message, "content", None)).strip()
        if text:
            return text

    if delegation_fallback:
        return delegation_fallback
    if tool_text_fallback:
        return tool_text_fallback
    return ""


def _iter_messages(value: Any):
    """Yield LangChain message objects from nested LangGraph update payloads."""
    if value is None:
        return
    if hasattr(value, "value"):
        yield from _iter_messages(getattr(value, "value"))
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_messages(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_messages(item)
        return
    if hasattr(value, "content"):
        yield value


def _is_assistant_message(message: Any) -> bool:
    message_type = getattr(message, "type", None)
    role = getattr(message, "role", None)
    if message_type in {"human", "tool", "system"} or role in {"user", "tool", "system"}:
        return False
    class_name = message.__class__.__name__.lower()
    return message_type == "ai" or role == "assistant" or "aimessage" in class_name


async def _fetch_state_answer(
    agent: Any,
    config: dict[str, Any],
    *,
    user_id: str,
    session_id: str,
    context: str,
) -> str:
    """Best-effort recovery of the latest assistant text from checkpointed state.

    LangGraph commits node outputs to the checkpointer as they complete. If the
    live `astream` pipeline fails after the LLM already produced its answer
    (e.g. tool error in a downstream node, payload shape confusing the walker),
    the answer is still persisted — this helper retrieves it.

    Never raises: a failed recovery is logged and returns "" so the caller can
    fall through to a normal error path.
    """
    try:
        state = await agent.aget_state(config)
    except Exception:  # noqa: BLE001 — recovery is informational only
        logger.exception(
            "farm_agent.state_fetch_failed context=%s user=%s session=%s",
            context, user_id, session_id,
        )
        return ""
    return _latest_assistant_text_from_state(state)


def _decide_after_recovery(
    stream_failed: bool,
    has_emitted_text: bool,
) -> tuple[bool, str | None]:
    """Decide what SSE signal to send after the recovery path runs.

    Args:
        stream_failed: True if the live astream loop raised an exception.
        has_emitted_text: True if any user-visible text was yielded
            (live tokens or recovered from state).

    Returns:
        (emit_error_event, optional_warning_text):
          - emit_error_event=True yields an SSE `error` event (frontend shows
            red banner via setError + replaces empty bubble with default text).
          - optional_warning_text, if non-None, is yielded as an SSE `warning`
            event for clients that want to surface a soft notice without the
            red banner. Currently the frontend ignores unknown events, so this
            is effectively reserved for future UI work.

    Policy (silent recovery for row D):
      A) ok + text       → no signal
      B) ok + no text    → error (LLM produced nothing — user-visible failure)
      C) failed + no text → error (unrecoverable)
      D) failed + text   → no signal (state recovery succeeded; logs carry the
         failure for ops, user sees a clean answer)
    """
    if not has_emitted_text:
        return True, None
    return False, None


@router.post("/ask", response_model=AskOut)
async def ask(
    payload: AskIn,
    request: Request,
    user: User = Depends(get_current_user),
) -> AskOut:
    """일회성 질의. 멀티턴 컨텍스트는 session_id로 유지된다.

    질의가 fast_path 패턴에 매칭되면 LLM 호출 없이 즉시 답한다 (지연 ↓ 비용 ↓).
    매칭 실패 시 정상 Deep Agent 흐름으로 위임.
    """
    session_id = payload.session_id or uuid.uuid4().hex

    # 1) Fast-path 시도 (LLM 우회)
    if settings.FARM_AGENT_FAST_PATH_ENABLED:
        try:
            fast_answer = await try_fast_path(payload.question, user.id)
        except Exception:  # noqa: BLE001 — fast-path 실패는 정상 흐름으로 폴백
            logger.exception("fast_path.error user=%s", user.id)
            fast_answer = None
        if fast_answer:
            logger.info("fast_path.hit user=%s session=%s", user.id, session_id)
            # ReasoningBank: fast-path hits feed the trajectory corpus too —
            # otherwise the most-frequent simple queries (weather, IoT lookup)
            # would be invisible to distillation, biasing strategies toward
            # complex queries only.
            record_trajectory_async(
                user_id=user.id,
                query=payload.question,
                route="fast_path",
                response_summary=fast_answer[:300],
                outcome="success",
            )
            return AskOut(answer=fast_answer, session_id=session_id, fast_path=True)

    # 2a) Deterministic exact-text 캐시 (Redis) — 모바일 더블탭 / F5 / polling 대응.
    #     LangCache 보다 먼저 — sub-ms 라 무료에 가까운 fast 경로.
    exact_cached = await answer_cache.lookup(payload.question, user_id=user.id)
    if exact_cached:
        logger.info("answer_cache.ask_hit user=%s session=%s", user.id, session_id)
        record_trajectory_async(
            user_id=user.id,
            query=payload.question,
            route="answer_cache",
            response_summary=exact_cached[:300],
            outcome="success",
        )
        return AskOut(answer=exact_cached, session_id=session_id)

    # 2b) LangCache 의미 캐시 조회 — paraphrase hit. ~50-200ms.
    #     fast_path · exact_cache 다음, 에이전트 호출 전 — 비용·지연 모두 큰 LLM 호출만 절약.
    cached_answer = await langcache.lookup(payload.question, user_id=user.id)
    if cached_answer:
        logger.info("langcache.ask_hit user=%s session=%s", user.id, session_id)
        # exact-cache 에도 저장해 다음 동일 질의는 ~3ms 로 처리.
        await answer_cache.store(payload.question, cached_answer, user_id=user.id)
        record_trajectory_async(
            user_id=user.id,
            query=payload.question,
            route="langcache",
            response_summary=cached_answer[:300],
            outcome="success",
        )
        return AskOut(answer=cached_answer, session_id=session_id)

    # 3) 정상 Deep Agent 흐름
    agent = _agent(request)
    config = _runtime_config(user.id, session_id)

    routed_question = _wrap_with_routing_hint(payload.question)
    try:
        state = await agent.ainvoke(
            {"messages": [{"role": "user", "content": routed_question}]},
            config=config,
        )
    except Exception:
        logger.exception("farm_agent.ask 실패 user=%s session=%s", user.id, session_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="에이전트 응답 생성 중 오류가 발생했습니다.",
        )

    answer = _latest_assistant_text_from_state(state)
    if answer:
        answer = await _maybe_reprompt_for_citation(
            agent,
            config,
            question=payload.question,
            answer=answer,
            user_id=user.id,
            session_id=session_id,
        )
        # 최종 답변(citation 재프롬프트 후 보정본 포함)을 두 캐시 모두에 적재:
        #   - LangCache  : 의미 유사 질의에서 hit 가능 (paraphrase)
        #   - answer_cache: exact 동일 텍스트에서 sub-ms hit
        await langcache.store(payload.question, answer, user_id=user.id)
        await answer_cache.store(payload.question, answer, user_id=user.id)
    # ReasoningBank: trajectory 기록 (fire-and-forget — 사용자 응답 지연 없음).
    # outcome 휴리스틱: 빈 응답이면 failed, "확인 어려움"·"문의" 같은 escalation 마커
    # 포함이면 uncertain, 그 외 success.
    _outcome = (
        "failed" if not answer
        else "uncertain" if any(m in (answer or "") for m in ("확인 어려움", "정보 없음", "문의"))
        else "success"
    )
    record_trajectory_async(
        user_id=user.id,
        query=payload.question,
        route="orchestrator",
        response_summary=(answer or "")[:300],
        outcome=_outcome,
    )
    return AskOut(answer=answer or "응답을 생성하지 못했습니다.", session_id=session_id)


@router.post("/stream")
async def stream(
    payload: AskIn,
    request: Request,
    user: User = Depends(get_current_user),
) -> EventSourceResponse:
    """SSE 토큰 스트림. astream(stream_mode="messages")로 LLM 토큰을 직접 흘려보낸다.

    이벤트:
      - `session`: 세션 ID (첫 이벤트)
      - `token`: LLM 토큰 청크 (content 문자열)
      - `tool`: 도구 호출 시작 알림 (도구명)
      - `done`: 스트림 종료
      - `error`: 예외 발생
    """
    session_id = payload.session_id or uuid.uuid4().hex

    # Fast-path: 단순 질의는 즉시 1회 토큰으로 흘려보낸다.
    if settings.FARM_AGENT_FAST_PATH_ENABLED:
        try:
            fast_answer = await try_fast_path(payload.question, user.id)
        except Exception:  # noqa: BLE001 — fast-path 실패는 정상 흐름으로 폴백
            logger.exception("fast_path.stream_error user=%s", user.id)
            fast_answer = None
        if fast_answer:
            async def fast_gen():
                yield {"event": "session", "data": session_id}
                yield {"event": "fast_path", "data": "1"}
                yield {"event": "token", "data": fast_answer}
                yield {"event": "done", "data": ""}
            return EventSourceResponse(fast_gen())

    # 2-tier cache: deterministic (sub-ms) → semantic (50-200ms) → LLM
    exact_cached = await answer_cache.lookup(payload.question, user_id=user.id)
    semantic_cached = exact_cached or await langcache.lookup(payload.question, user_id=user.id)
    if semantic_cached:
        cache_kind = "exact" if exact_cached else "semantic"
        logger.info("agent_cache.stream_hit kind=%s user=%s session=%s",
                    cache_kind, user.id, session_id)
        # semantic hit 만 적중했으면 다음 동일 질의는 exact hit 로 처리되도록 promote.
        if not exact_cached:
            await answer_cache.store(payload.question, semantic_cached, user_id=user.id)

        async def cached_gen():
            yield {"event": "session", "data": session_id}
            yield {"event": "cache", "data": cache_kind}
            yield {"event": "token", "data": semantic_cached}
            try:
                citations = _extract_citations(semantic_cached)
                if citations:
                    yield {
                        "event": "citations",
                        "data": _json.dumps(citations, ensure_ascii=False),
                    }
            except Exception:  # noqa: BLE001
                pass
            yield {"event": "done", "data": ""}
        return EventSourceResponse(cached_gen())

    agent = _agent(request)
    config = _runtime_config(user.id, session_id)

    # Per-user agent lock — prevents two open SSE sessions (e.g. two browser
    # tabs) from racing on the same checkpointer thread_id. Lock is best-effort:
    # Redis unavailable returns a sentinel and we proceed (the IoT relay's own
    # idempotency is the second line of defense for actuator-side effects).
    from app.services.farm_agent.locks import (
        acquire_user_agent_lock,
        refresh_user_agent_lock,
        release_user_agent_lock,
    )

    async def gen():
        import asyncio as _asyncio
        yield {"event": "session", "data": session_id}

        lock_token = await acquire_user_agent_lock(user.id)
        if lock_token is None:
            logger.info("stream.lock_busy user=%s session=%s", user.id, session_id)
            yield {
                "event": "error",
                "data": (
                    "다른 세션에서 에이전트가 실행 중입니다. "
                    "기존 세션을 닫고 다시 시도해주세요."
                ),
            }
            yield {"event": "done", "data": ""}
            return
        emitted_tool_call_ids: set[str] = set()
        # heartbeat: 토큰 사이의 idle 시간이 길면 SSE 프록시(nginx 등)가 끊는다.
        # FARM_AGENT_SSE_HEARTBEAT_SEC 마다 ping 이벤트를 송출해 keep-alive 유지.
        heartbeat_interval = settings.FARM_AGENT_SSE_HEARTBEAT_SEC
        emitted_text = ""
        # Dedup AIMessage IDs we've already emitted tokens for. With Deep Agents
        # (orchestrator + subagent + memory middleware) and stream_mode="updates",
        # the SAME completed AIMessage can show up across multiple node updates —
        # e.g. once from the agent node, again as it's mirrored into a memory or
        # state-aggregation channel. Without dedup, the frontend appends the
        # full answer twice and the user sees the same response repeated.
        emitted_message_ids: set[str] = set()
        # Skip-synthesis flag: once the orchestrator delegated to a subagent and
        # the subagent's `task` ToolMessage produced a complete user-visible
        # answer, suppress any subsequent orchestrator AIMessage content. The
        # orchestrator's post-task synthesis is just paraphrasing what the
        # subagent already said — emitting it duplicates the bubble and adds
        # ~2-5s of perceived latency for no quality gain.
        suppress_orchestrator_synthesis = False
        # Deep Agents: when orchestrator delegates via `task` and produces no
        # final synthesis (Gemma habit), the subagent's full answer arrives as
        # a ToolMessage and would otherwise be dropped by the assistant filter.
        # Buffer the most recent one and emit it at end-of-stream as fallback.
        delegation_fallback_text = ""
        # Resume-replay guard: when LangGraph resumes a checkpointed thread for
        # turn N (N>1), `stream_mode="updates"` can include prior turn's
        # AIMessages and delegation ToolMessages in the initial replay payload.
        # Without seeding the dedup sets, those re-emit as if they were new
        # tokens — and worse, the prior delegation ToolMessage flips
        # `suppress_orchestrator_synthesis` to True, which then suppresses
        # this turn's REAL answer. User reports: "previous answer shows up
        # and nothing happens". Seed the dedup sets from checkpointer state
        # so prior turns are filtered before they reach the wire.
        seen_delegation_keys: set[str] = set()
        try:
            _prior_state = await agent.aget_state(config)
            _prior_msgs = (
                getattr(_prior_state, "values", {}) or {}
            ).get("messages", [])
            for _m in _prior_msgs:
                if _is_assistant_message(_m):
                    _mid = getattr(_m, "id", None)
                    if _mid:
                        emitted_message_ids.add(_mid)
                    _content = _content_to_text(getattr(_m, "content", None))
                    if _content:
                        emitted_message_ids.add(
                            f"hash:{hash(_content)}:{len(_content)}"
                        )
                if _is_subagent_delegation_tool_message(_m):
                    _tcid = getattr(_m, "tool_call_id", "") or ""
                    _ttext = _content_to_text(getattr(_m, "content", None)).strip()
                    if _tcid:
                        seen_delegation_keys.add(f"tcid:{_tcid}")
                    if _ttext:
                        seen_delegation_keys.add(
                            f"hash:{hash(_ttext)}:{len(_ttext)}"
                        )
        except Exception:  # noqa: BLE001 — first turn / no prior state
            pass
        stream_failed = False
        stream_failure_detail = ""
        routed_question = _wrap_with_routing_hint(payload.question)
        try:
            stream = agent.astream(
                {"messages": [{"role": "user", "content": routed_question}]},
                config=config,
                stream_mode="updates",
            )
            stream_iter = stream.__aiter__()
            next_task = _asyncio.create_task(stream_iter.__anext__())
            while True:
                try:
                    item = await _asyncio.wait_for(
                        _asyncio.shield(next_task),
                        timeout=heartbeat_interval,
                    )
                except _asyncio.TimeoutError:
                    # 토큰 없이 N초 경과 → 프록시 idle 끊김 방지를 위한 ping.
                    # 중요: shield 없이 wait_for를 쓰면 pending __anext__ task가
                    # 취소되어 LangGraph stream 자체가 중간 종료된다.
                    # Refresh the per-user lock at the same cadence so a long
                    # subsidy/diagnosis turn (>90s) doesn't TTL out and let a
                    # second tab in.
                    await refresh_user_agent_lock(user.id, lock_token)
                    yield {"event": "ping", "data": ""}
                    continue
                except StopAsyncIteration:
                    break

                # Defensive: a single malformed update payload (e.g. unexpected
                # Send/Command/Interrupt object that confuses the walker) should
                # not abort the whole stream — log it and drain the next update.
                try:
                    for message in _iter_messages(item):
                        # 도구 호출 이벤트 — 같은 tool_call_id가 여러 업데이트에 걸쳐
                        # 나타날 수 있으므로 ID 단위로 한 번만 emit.
                        tool_calls = getattr(message, "tool_calls", None)
                        if tool_calls:
                            for tc in tool_calls:
                                if isinstance(tc, dict):
                                    tc_id = tc.get("id") or ""
                                    tc_name = tc.get("name") or ""
                                    tc_args = tc.get("args") or tc.get("arguments") or {}
                                else:
                                    tc_id = getattr(tc, "id", "") or ""
                                    tc_name = getattr(tc, "name", "") or ""
                                    tc_args = getattr(tc, "args", None) or {}
                                dedupe_key = tc_id or f"{tc_name}:{len(emitted_tool_call_ids)}"
                                if tc_name and dedupe_key not in emitted_tool_call_ids:
                                    emitted_tool_call_ids.add(dedupe_key)
                                    yield {"event": "tool", "data": tc_name}
                                    # Surface the call payload so the UI can show
                                    # WHAT was queried, not just the tool name.
                                    if tc_name in _PLAN_TOOL_NAMES:
                                        plan_md = ""
                                        if isinstance(tc_args, dict):
                                            todos = tc_args.get("todos") or tc_args.get("plan") or []
                                            if isinstance(todos, list):
                                                plan_md = "\n".join(
                                                    f"- {t.get('content', t)}" if isinstance(t, dict) else f"- {t}"
                                                    for t in todos
                                                )
                                        if plan_md:
                                            yield {"event": "plan", "data": plan_md}
                                    elif tc_name in _SUBAGENT_DELEGATION_TOOL_NAMES:
                                        sub = ""
                                        if isinstance(tc_args, dict):
                                            sub = (
                                                tc_args.get("subagent_type")
                                                or tc_args.get("name")
                                                or tc_args.get("agent")
                                                or ""
                                            )
                                        if sub:
                                            yield {"event": "subagent", "data": str(sub)}
                                    else:
                                        try:
                                            # NOTE: 변수명을 `payload` 로 쓰면 안 된다 —
                                            # 외부 핸들러 함수의 `payload: AskIn` 파라미터와
                                            # 같은 이름이라 Python 이 본 generator 스코프에
                                            # `payload` 를 로컬로 묶어버려 `payload.question`
                                            # 첫 참조에서 UnboundLocalError 가 발생한다.
                                            tool_input_json = _json.dumps(
                                                {
                                                    "tool_call_id": tc_id,
                                                    "name": tc_name,
                                                    "args": tc_args if isinstance(tc_args, (dict, list, str, int, float, bool)) or tc_args is None else str(tc_args),
                                                },
                                                ensure_ascii=False,
                                                default=str,
                                            )
                                            yield {"event": "tool_input", "data": tool_input_json}
                                        except Exception:  # noqa: BLE001
                                            pass

                        # Subagent-delegation tool return: stream it AS the answer
                        # immediately. Then suppress any orchestrator post-task
                        # synthesis (which would just paraphrase this same text).
                        if _is_subagent_delegation_tool_message(message):
                            tool_text = _content_to_text(
                                getattr(message, "content", None)
                            ).strip()
                            # Resume-replay guard: skip delegation ToolMessages
                            # that already exist in the prior thread state. They
                            # would otherwise mask THIS turn's real delegation
                            # by flipping suppress_orchestrator_synthesis early.
                            _tcid = getattr(message, "tool_call_id", "") or ""
                            _replay_keys = []
                            if _tcid:
                                _replay_keys.append(f"tcid:{_tcid}")
                            if tool_text:
                                _replay_keys.append(
                                    f"hash:{hash(tool_text)}:{len(tool_text)}"
                                )
                            if any(k in seen_delegation_keys for k in _replay_keys):
                                logger.debug(
                                    "farm_agent.replay_skip user=%s session=%s "
                                    "kind=delegation tcid=%s len=%d",
                                    user.id, session_id, _tcid, len(tool_text),
                                )
                                continue
                            # Track this turn's delegation as well so subsequent
                            # update payloads in the SAME stream don't duplicate.
                            for _k in _replay_keys:
                                seen_delegation_keys.add(_k)
                            if tool_text:
                                # Verifier-agent FAIL/UNKNOWN mining (iter 19).
                                # No-op on PASS, on non-verifier delegations
                                # (no verdict prefix), or on I/O errors. Best-
                                # effort — must never break the SSE stream.
                                try:
                                    record_verifier_verdict(
                                        tool_text,
                                        question=payload.question,
                                        user_id=user.id,
                                        session_id=session_id,
                                    )
                                except Exception:  # noqa: BLE001
                                    pass
                                delegation_fallback_text = tool_text
                                # Only emit the FIRST delegation result — multi-subagent
                                # flows fall back to the original "wait then surface"
                                # path so the orchestrator can synthesize across them.
                                if not suppress_orchestrator_synthesis:
                                    emitted_text += tool_text
                                    suppress_orchestrator_synthesis = True
                                    yield {"event": "token", "data": tool_text}
                            continue

                        # Non-delegation ToolMessages: surface their output to the
                        # UI as a tool_output event. Truncated to keep SSE small.
                        message_type = getattr(message, "type", None)
                        role = getattr(message, "role", None)
                        if message_type == "tool" or role == "tool":
                            tool_name = getattr(message, "name", "") or ""
                            tool_text = _content_to_text(
                                getattr(message, "content", None)
                            ).strip()
                            if tool_text:
                                truncated = tool_text[:1800]
                                if len(tool_text) > 1800:
                                    truncated += "…"
                                try:
                                    yield {
                                        "event": "tool_output",
                                        "data": _json.dumps(
                                            {
                                                "tool_call_id": getattr(message, "tool_call_id", "") or "",
                                                "name": tool_name,
                                                "result": truncated,
                                            },
                                            ensure_ascii=False,
                                        ),
                                    }
                                except Exception:  # noqa: BLE001
                                    pass
                            continue

                        if not _is_assistant_message(message):
                            continue
                        # Dedup by message ID — the same completed AIMessage can
                        # appear in multiple updates (memory mirror, state agg).
                        # Fall back to a content-hash-based key when ID missing.
                        msg_id = getattr(message, "id", None) or ""
                        text = _content_to_text(getattr(message, "content", None))
                        if not text:
                            continue
                        # Skip orchestrator synthesis after we already streamed
                        # the subagent's verbatim answer (Fix #1). The orchestrator
                        # still ran on the LLM (no wall-clock saving) but the user
                        # sees the answer the moment the subagent finishes.
                        if suppress_orchestrator_synthesis:
                            logger.debug(
                                "farm_agent.skip_orch_synthesis user=%s session=%s len=%d",
                                user.id, session_id, len(text),
                            )
                            continue
                        dedup_key = msg_id or f"hash:{hash(text)}:{len(text)}"
                        if dedup_key in emitted_message_ids:
                            logger.debug(
                                "farm_agent.stream_dedup_skip user=%s session=%s id=%s len=%d",
                                user.id, session_id, msg_id or "(no-id)", len(text),
                            )
                            continue
                        emitted_message_ids.add(dedup_key)
                        emitted_text += text
                        yield {"event": "token", "data": text}
                except Exception:  # noqa: BLE001 — single bad payload shouldn't kill the stream
                    logger.exception(
                        "farm_agent.stream_payload_skipped user=%s session=%s",
                        user.id, session_id,
                    )
                next_task = _asyncio.create_task(stream_iter.__anext__())
        except Exception as exc:  # noqa: BLE001 — recover from state, then notify client
            stream_failed = True
            stream_failure_detail = f"{type(exc).__name__}: {exc}"
            # WARNING level so operators can see the failure type without enabling
            # debug logging. Full traceback follows via logger.exception below.
            logger.warning(
                "farm_agent.stream_failed user=%s session=%s err_type=%s err=%s",
                user.id, session_id, type(exc).__name__, exc,
            )
            logger.exception(
                "farm_agent.stream_traceback user=%s session=%s",
                user.id, session_id,
            )
            # In dev environments (DEBUG=True), surface the failure detail to
            # the client SSE so the developer can diagnose without tailing logs.
            if getattr(settings, "DEBUG", False):
                yield {"event": "warning", "data": f"[debug] {stream_failure_detail}"}

        try:
            # Recovery path: runs whether the stream finished cleanly with no
            # text emitted, OR crashed partway. LangGraph persists node outputs
            # incrementally, so the LLM's final answer is often already in the
            # checkpointer even when the live stream broke.
            #
            # IMPORTANT: only run recovery when the live stream produced NOTHING.
            # If even one token was streamed, the recovery state may contain the
            # SAME final answer assembled — emitting it again duplicates the bubble.
            if not emitted_text.strip():
                recovered = await _fetch_state_answer(
                    agent, config,
                    user_id=user.id,
                    session_id=session_id,
                    context="after_failure" if stream_failed else "after_clean_exit",
                )
                # Dedup: don't re-emit a recovery answer that's already been
                # streamed (covers the edge case where emitted_text was emptied
                # by a downstream filter but the state still has the same text).
                if recovered and recovered.strip() != emitted_text.strip():
                    yield {"event": "token", "data": recovered}
                    emitted_text = recovered
                elif (
                    delegation_fallback_text
                    and delegation_fallback_text.strip() != emitted_text.strip()
                ):
                    # Orchestrator delegated via `task` but never produced its
                    # own synthesis; surface the subagent's answer directly.
                    logger.info(
                        "farm_agent.delegation_fallback_used user=%s session=%s len=%d",
                        user.id, session_id, len(delegation_fallback_text),
                    )
                    yield {"event": "token", "data": delegation_fallback_text}
                    emitted_text = delegation_fallback_text

            # If we still have nothing, log a structured diagnostic snapshot so
            # the next reproduction tells us WHY: how many tool calls fired,
            # whether any subagent was dispatched, and recent message types.
            if not emitted_text.strip():
                try:
                    state = await agent.aget_state(config)
                    values = getattr(state, "values", state)
                    msgs = (
                        values.get("messages", []) if isinstance(values, dict) else []
                    )
                    type_counts: dict[str, int] = {}
                    for m in msgs[-10:]:
                        t = getattr(m, "type", None) or getattr(m, "role", None) or m.__class__.__name__
                        type_counts[str(t)] = type_counts.get(str(t), 0) + 1
                    logger.warning(
                        "farm_agent.empty_response user=%s session=%s "
                        "stream_failed=%s tool_calls=%d delegation_buffer_len=%d "
                        "recent_msg_types=%s total_msgs=%d",
                        user.id, session_id, stream_failed,
                        len(emitted_tool_call_ids),
                        len(delegation_fallback_text),
                        type_counts, len(msgs),
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "farm_agent.empty_response_diag_failed user=%s session=%s",
                        user.id, session_id,
                    )

            emit_error, warning_text = _decide_after_recovery(
                stream_failed=stream_failed,
                has_emitted_text=bool(emitted_text.strip()),
            )
            if warning_text:
                yield {"event": "warning", "data": warning_text}
            if emit_error:
                # 사용자에게 더 구체적인 안내. stream_failed=True 이면 인프라 오류,
                # 아니면 모델이 빈 응답을 낸 경우 — 후자는 보통 새 스레드 시작으로 해결.
                if stream_failed:
                    base = "에이전트 서버 통신에 실패했습니다."
                    # DEBUG 모드에서는 사용자에게 정확한 예외를 그대로 보여줘서
                    # 운영자가 로그를 따로 안 봐도 진단 가능. 운영 환경에서는
                    # DEBUG=False 로 잠그면 일반 메시지만 노출된다.
                    if getattr(settings, "DEBUG", False) and stream_failure_detail:
                        user_msg = f"{base}\n\n[debug] {stream_failure_detail}"
                    else:
                        user_msg = f"{base} 잠시 후 다시 시도해주세요."
                else:
                    user_msg = (
                        "에이전트가 응답을 비웠습니다. '새 대화' 버튼으로 스레드를 "
                        "초기화하거나, 질문을 더 구체적으로 다시 입력해주세요."
                    )
                yield {"event": "error", "data": user_msg}

            # Post-stream enrichment: parse the final answer for [doc > 제N조]
            # citation chips and IoT-action proposals (HITL approval cards).
            # Both events are best-effort and only emit when patterns match.
            final_text = emitted_text.strip()
            if final_text:
                citations: list[dict[str, str]] = []
                try:
                    citations = _extract_citations(final_text)
                    if citations:
                        yield {
                            "event": "citations",
                            "data": _json.dumps(citations, ensure_ascii=False),
                        }
                except Exception:  # noqa: BLE001
                    citations = []
                # Citation guardrail: subsidy answers without a [doc > 제N조]
                # citation are low-confidence — surface a soft signal so the UI
                # can render a "근거 없음 — 시행지침 원문 확인 권장" notice and the
                # eval/observability stack can flag the regression. Non-blocking
                # by design — re-prompting the subagent is a heavier follow-up
                # tracked in the ralph ledger.
                guardrail_fired = False
                try:
                    if not citations and _is_subsidy_domain_answer(payload.question, final_text):
                        guardrail_fired = True
                        logger.warning(
                            "farm_agent.citation_guard.missing user=%s session=%s "
                            "question=%r",
                            user.id, session_id, payload.question[:100],
                        )
                        yield {
                            "event": "low_confidence",
                            "data": _json.dumps(
                                {
                                    "reason": "missing_citation",
                                    "domain": "subsidy",
                                    "hint": "시행지침 원문 인용이 없어 신뢰도가 낮습니다. 직불 담당자 확인을 권장합니다.",
                                },
                                ensure_ascii=False,
                            ),
                        }
                except Exception:  # noqa: BLE001
                    pass

                # Citation re-prompt for /stream — only when guardrail fired, the
                # feature flag is on, and we have a usable agent + config. Run as
                # a single ainvoke (not astream) to keep the SSE loop simple; the
                # corrected answer is emitted as one `retry` event that the
                # frontend uses to replace the message content. This avoids
                # interleaving two streaming sessions in one SSE response.
                if guardrail_fired and settings.FARM_AGENT_CITATION_REPROMPT_ENABLED:
                    try:
                        retried_text = await _maybe_reprompt_for_citation(
                            agent, config,
                            question=payload.question,
                            answer=final_text,
                            user_id=user.id,
                            session_id=session_id,
                        )
                        retry_citations = _extract_citations(retried_text or "")
                        if retried_text and retried_text != final_text and retry_citations:
                            yield {"event": "retry",
                                   "data": _json.dumps(
                                       {"reason": "citation_added",
                                        "content": retried_text},
                                       ensure_ascii=False)}
                            yield {"event": "citations",
                                   "data": _json.dumps(retry_citations,
                                                       ensure_ascii=False)}
                            # Update final_text so the IoT-action heuristic below
                            # matches on the corrected answer, not the rejected one.
                            final_text = retried_text
                    except Exception:  # noqa: BLE001 — never break the stream
                        logger.exception(
                            "farm_agent.stream_reprompt_failed user=%s session=%s",
                            user.id, session_id,
                        )
                try:
                    proposal = _looks_like_iot_action_proposal(final_text)
                    if proposal:
                        yield {
                            "event": "action",
                            "data": _json.dumps(
                                {
                                    "kind": "iot_control",
                                    "control_type": proposal["control_type"],
                                    "summary": final_text[:280],
                                },
                                ensure_ascii=False,
                            ),
                        }
                except Exception:  # noqa: BLE001
                    pass

                # 두 캐시 모두 적재 — citation 재프롬프트 후의 최종(보정) 답변만.
                # IoT 제어 제안은 HITL 승인 단계가 별도이므로 캐시 적재 대상에서 제외.
                if not _looks_like_iot_action_proposal(final_text):
                    await langcache.store(payload.question, final_text, user_id=user.id)
                    await answer_cache.store(payload.question, final_text, user_id=user.id)
        finally:
            # 클라이언트가 done 이벤트를 기다리고 있으므로 예외 여부와 무관하게 반드시 종료 신호 emit.
            yield {"event": "done", "data": ""}
            # Release the per-user agent lock — Lua compare-and-delete so we
            # only delete OUR token, never a successor's. No-op if Redis was
            # unavailable (lock_token is sentinel).
            await release_user_agent_lock(user.id, lock_token)

    return EventSourceResponse(gen())


# ── 신규 agentic 엔드포인트 ──────────────────────────────────────────────────


class BriefingOut(BaseModel):
    date: str = Field(description="브리핑 기준 날짜 (YYYY-MM-DD)")
    content: str = Field(description="마크다운 브리핑 본문")
    cached: bool = Field(default=False, description="캐시 적중 여부")


@router.get("/briefing", response_model=BriefingOut)
async def briefing(
    request: Request,
    refresh: bool = False,
    user: User = Depends(get_current_user),
) -> BriefingOut:
    """오늘의 자동 생성 농민 브리핑 (날씨 + 시세 + IoT 이력 + 일지 + 권장 작업).

    캐시: (user_id, today)당 1회 생성, refresh=true 로 재생성.
    Stateful — thread_id `briefing:{user_id}`로 어제 브리핑을 참고해 차이만 강조.
    """
    agent = _agent(request)
    today = date.today()
    try:
        content, cached = await get_or_generate_briefing(
            agent=agent,
            user_id=user.id,
            user_name=user.name or user.id,
            target_date=today,
            force_regenerate=refresh,
        )
    except Exception:
        logger.exception("briefing.generate_failed user=%s", user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="브리핑 생성 중 오류가 발생했습니다.",
        )
    # ReasoningBank: 브리핑 트라젝토리 기록 (cached hit 은 LLM 호출이 없으므로 제외).
    if not cached:
        record_trajectory_async(
            user_id=user.id,
            query="[briefing]",
            route="briefing",
            response_summary=(content or "")[:300],
            outcome="success" if content and len(content) > 200 else "failed",
        )
    return BriefingOut(date=today.isoformat(), content=content, cached=cached)


class DistillOut(BaseModel):
    trajectories_read: int = Field(description="distill 에 사용된 트라젝토리 수")
    new_strategies: int = Field(description="STRATEGIES.md 에 추가된 신규 전략 수")
    appended: bool = Field(description="파일이 실제로 갱신됐는지")


@router.post("/distill-strategies", response_model=DistillOut)
async def distill_strategies_endpoint(
    days: int = 7,
    max_trajectories: int = 50,
    user: User = Depends(get_current_user),
) -> DistillOut:
    """ReasoningBank: 최근 N 일 트라젝토리에서 새 전략을 도출해 STRATEGIES.md 에 append.

    수동 트리거 (또는 cron) — 매 사용자 요청에 동기 실행 금지 (비용 발생).
    Admin allowlist (settings.FARM_AGENT_ADMIN_USER_IDS, CSV) 에 등록된 사용자만
    호출 가능. 미설정 시 거부 — LLM cost 가 발생하는 endpoint 라 prod 에서는 명시적
    화이트리스트가 필수.
    """
    raw = (getattr(settings, "FARM_AGENT_ADMIN_USER_IDS", "") or "").strip()
    allowed = {s.strip() for s in raw.split(",") if s.strip()}
    if not allowed or str(user.id) not in allowed:
        logger.warning("distill_strategies.denied user=%s allowlist_set=%s", user.id, bool(allowed))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="이 작업은 관리자만 수행할 수 있습니다.",
        )
    stats = await distill_strategies(days=days, max_trajectories=max_trajectories)
    logger.info("distill_strategies user=%s stats=%s", user.id, stats)
    return DistillOut(**stats)


class DiagnoseImageOut(BaseModel):
    pest: str | None = Field(default=None, description="자동 분류된 해충/질병명")
    crop: str = Field(description="진단 작물 (요청 또는 사용자 프로필에서 추론)")
    region: str = Field(description="진단 지역 (요청 또는 사용자 프로필에서 추론)")
    image_url: str | None = None
    answer: str = Field(description="에이전트 종합 진단 마크다운")
    session_id: str


def _persist_diagnosis_image(contents: bytes, filename: str | None) -> str:
    """업로드 이미지를 WebP로 변환·리사이징해 정적 디렉터리에 저장하고 공개 URL 반환.

    실패 시 (디스크 가득, 손상된 이미지) 빈 문자열 반환 — 진단 자체는 계속 진행.
    """
    try:
        Image.MAX_IMAGE_PIXELS = 24_000_000
        img = Image.open(io.BytesIO(contents))
        img.thumbnail((640, 640))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        file_id = uuid.uuid4().hex
        out_path = _DIAGNOSIS_UPLOAD_DIR / f"{file_id}.webp"
        img.save(out_path, "WEBP", quality=85)
        return f"/uploads/diagnosis/{file_id}.webp"
    except Image.DecompressionBombError:
        logger.warning("diagnose_image.bomb_rejected filename=%s", filename)
        return ""
    except Exception:  # noqa: BLE001 — 저장 실패는 graceful (진단은 계속)
        logger.exception("diagnose_image.persist_failed filename=%s", filename)
        return ""


@router.post("/diagnose-image", response_model=DiagnoseImageOut)
async def diagnose_image(
    request: Request,
    file: UploadFile = File(..., description="해충 의심 부위 사진"),
    crop: str | None = Form(default=None, description="작물 (없으면 사용자 main_crop 사용)"),
    region: str | None = Form(default=None, description="지역 (없으면 사용자 location 사용)"),
    user: User = Depends(get_current_user),
) -> DiagnoseImageOut:
    """멀티모달 진단: 이미지 업로드 → 자동 해충 분류 → 에이전트 종합 진단.

    기존 2단계 흐름(/diagnosis/upload + /diagnosis/history) 을 1회 호출로 통합.
    이미지가 분류되면 농약 정보 포함 안전 검증(verifier-agent) 이 자동 적용된다.
    """
    contents = await _read_with_size_cap(file, _MAX_IMAGE_BYTES)

    # 1) 해충 분류 (RunPod) — 실패 시 graceful: pest=None 으로 진행 (사용자에게 추가 정보 요청 흐름)
    # 단, ClassifierConfigError(URL 미설정 등 환경 문제)와 ClassifierError(런타임 실패)를 분리 로깅:
    # 전자는 운영자가 즉시 인지해야 할 설정 누락이므로 ERROR, 후자는 일시적이므로 WARNING.
    pest_name: str | None = None
    try:
        result = await classify_pest_image(
            contents,
            filename=file.filename or "image.jpg",
            content_type=file.content_type or "image/jpeg",
        )
        pest_name = result.get("pred")
    except ClassifierConfigError as exc:
        logger.error("diagnose_image.classifier_unconfigured err=%s", exc)
    except ClassifierError as exc:
        logger.warning("diagnose_image.classify_failed err=%s", exc)

    # 2) 이미지 영구 저장 (응답에 image_url 노출)
    image_url = _persist_diagnosis_image(contents, file.filename) or None

    # 3) 컨텍스트 파라미터 결정 (요청 > 사용자 프로필 폴백)
    final_crop = crop or user.main_crop or "전체 작물"
    final_region = region or user.location or "위치 미상"

    # 4) 에이전트로 진단 위임 (verifier-agent 자동 호출됨 — orchestrator prompt 의무)
    agent = _agent(request)
    session_id = uuid.uuid4().hex
    config = _runtime_config(user.id, session_id)

    if pest_name and pest_name not in ("정상", "알 수 없음"):
        prompt = (
            f"다음 정보로 병해충 진단을 수행해주세요:\n"
            f"- pest: {pest_name}\n"
            f"- crop: {final_crop}\n"
            f"- region: {final_region}\n"
            f"diagnosis-agent에 위임 후 verifier-agent로 안전 검증까지 완료하세요."
        )
    elif pest_name == "정상":
        return DiagnoseImageOut(
            pest="정상",
            crop=final_crop,
            region=final_region,
            image_url=image_url,
            answer=(
                "🔍 분석 결과 병해충이 감지되지 않았습니다. **정상** 상태입니다.\n\n"
                "현재 상태를 잘 유지하시고, 정기적인 예찰을 통해 초기 유입을 예방하세요."
            ),
            session_id=session_id,
        )
    else:
        # 분류 실패 — 사용자에게 추가 정보 요청
        prompt = (
            f"이미지 자동 분류에 실패했습니다. 사용자({user.name})의 작물 {final_crop} "
            f"({final_region})에 흔한 병해충 3-5가지를 제시하고, 사용자가 어떤 증상을 보이는지 "
            f"한 줄 질문으로 마무리해주세요. 농약 추천은 하지 마세요."
        )

    try:
        state = await agent.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]},
            config=config,
        )
    except Exception:
        logger.exception("diagnose_image.agent_failed user=%s", user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="진단 에이전트 호출에 실패했습니다.",
        )

    answer = _latest_assistant_text_from_state(state)
    return DiagnoseImageOut(
        pest=pest_name,
        crop=final_crop,
        region=final_region,
        image_url=image_url,
        answer=answer or "진단 결과를 생성하지 못했습니다.",
        session_id=session_id,
    )


class VoiceAskOut(BaseModel):
    transcript: str = Field(description="STT 전사 텍스트 (사용자 발화)")
    answer: str = Field(description="에이전트 응답 마크다운")
    session_id: str
    fast_path: bool = False


@router.post("/voice", response_model=VoiceAskOut)
async def voice(
    request: Request,
    file: UploadFile = File(..., description="webm/mp3/wav 오디오 파일"),
    session_id: str | None = Form(default=None),
    user: User = Depends(get_current_user),
) -> VoiceAskOut:
    """음성 → STT → 에이전트 응답 (한 번의 요청으로 완결).

    Whisper prompt 도메인 힌트로 한국어 농업 용어 전사 정확도를 높인다.
    전사된 텍스트는 ask 엔드포인트와 동일한 fast-path / Deep Agent 흐름을 탄다.
    """
    audio_bytes = await _read_with_size_cap(file, _MAX_AUDIO_BYTES)

    # 1) STT — 농업 도메인 힌트 주입 (Whisper prompt)
    domain_hint = (
        "FarmOS 농업 챗봇. 자주 등장하는 단어: "
        "직불금, 공익직불, 노균병, 진딧물, 응애, 도열병, 토마토, 사과, 딸기, "
        "관수, 환기, 차광, 시비, 살포, 희석배수, NCPMS, KAMIS, 시세."
    )
    try:
        transcript = await transcribe_audio(
            audio_bytes,
            filename=file.filename or "audio.webm",
            content_type=file.content_type or "audio/webm",
            language="ko",
            prompt=domain_hint,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    except Exception:
        logger.exception("voice.stt_failed user=%s", user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="음성 전사에 실패했습니다.",
        )

    if not transcript:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="전사된 텍스트가 비어있습니다. 다시 녹음해주세요.",
        )

    # 2) 에이전트 호출 — fast-path 우선 시도 (음성도 동일 최적화 적용)
    sid = session_id or uuid.uuid4().hex
    if settings.FARM_AGENT_FAST_PATH_ENABLED:
        try:
            fast_answer = await try_fast_path(transcript, user.id)
        except Exception:  # noqa: BLE001 — fast-path 실패는 정상 흐름으로 폴백
            # /ask, /stream 과 동일하게 명시적 로깅 — 음성 endpoint 만 누락되어 있던 일관성 문제 수정
            logger.exception("voice.fast_path_error user=%s", user.id)
            fast_answer = None
        if fast_answer:
            return VoiceAskOut(
                transcript=transcript,
                answer=fast_answer,
                session_id=sid,
                fast_path=True,
            )

    agent = _agent(request)
    config = _runtime_config(user.id, sid)
    routed_transcript = _wrap_with_routing_hint(transcript)
    try:
        state = await agent.ainvoke(
            {"messages": [{"role": "user", "content": routed_transcript}]},
            config=config,
        )
    except Exception:
        logger.exception("voice.agent_failed user=%s", user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="에이전트 응답 생성에 실패했습니다.",
        )

    answer = _latest_assistant_text_from_state(state)
    return VoiceAskOut(
        transcript=transcript,
        answer=answer or "응답을 생성하지 못했습니다.",
        session_id=sid,
    )


# ── 스레드 목록 / 단일 스레드 조회 ──────────────────────────────────────────
#
# Postgres checkpointer 가 thread_id 단위로 모든 multi-turn 상태를 저장하므로
# 별도 schema 없이 메타데이터만 추출해 사용자 사이드바에 노출한다.


class ThreadSummary(BaseModel):
    session_id: str
    last_user_message: str = Field(description="가장 최근 사용자 발화 (제목 후보)")
    updated_at: datetime | None = None
    message_count: int = 0


class ThreadMessage(BaseModel):
    role: str
    content: str
    timestamp: datetime | None = None


class ThreadDetail(BaseModel):
    session_id: str
    messages: list[ThreadMessage]


def _checkpointer(request: Request):
    """Resolve the LangGraph checkpointer attached to the running agent.

    The agent is built once at lifespan startup, so accessing the .checkpointer
    attribute is cheap. Returns None if the agent (or its checkpointer) hasn't
    been initialized — caller short-circuits to an empty list.
    """
    agent = getattr(request.app.state, "farm_agent", None)
    if agent is None:
        return None
    return getattr(agent, "checkpointer", None)


@router.get("/threads", response_model=list[ThreadSummary])
async def list_threads(
    request: Request,
    limit: int = 30,
    user: User = Depends(get_current_user),
) -> list[ThreadSummary]:
    """현재 사용자의 최근 대화 스레드 목록.

    thread_id 패턴 `f"{user_id}:{session_id}"` 로 저장되므로 prefix 매칭으로
    필터링한다. checkpointer 가 alist() / aget_tuple() 을 지원하면 사용하고,
    없으면 빈 리스트로 graceful fallback 한다.
    """
    saver = _checkpointer(request)
    if saver is None:
        return []

    user_prefix = f"{user.id}:"
    summaries: dict[str, ThreadSummary] = {}

    list_method = getattr(saver, "alist", None) or getattr(saver, "list", None)
    if list_method is None:
        return []

    try:
        # alist returns CheckpointTuple — we only need .config.thread_id and
        # .checkpoint.channel_values for the most recent user message.
        async for item in list_method(None, limit=limit * 4):  # type: ignore[func-returns-value]
            cfg = getattr(item, "config", {}) or {}
            configurable = cfg.get("configurable", {}) if isinstance(cfg, dict) else {}
            thread_id = configurable.get("thread_id") or ""
            if not thread_id.startswith(user_prefix):
                continue
            session_id = thread_id[len(user_prefix):]
            if not session_id or session_id in summaries:
                continue
            checkpoint = getattr(item, "checkpoint", {}) or {}
            channel_values = (
                checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
            )
            messages = channel_values.get("messages", []) if isinstance(channel_values, dict) else []
            last_user = ""
            for msg in reversed(messages):
                if _is_human_message(msg):
                    last_user = _content_to_text(getattr(msg, "content", None)).strip()
                    if last_user:
                        break
            ts_raw = (
                checkpoint.get("ts")
                if isinstance(checkpoint, dict)
                else None
            )
            updated_at: datetime | None = None
            if isinstance(ts_raw, str):
                try:
                    updated_at = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except ValueError:
                    updated_at = None
            summaries[session_id] = ThreadSummary(
                session_id=session_id,
                last_user_message=last_user[:120] or "(빈 스레드)",
                updated_at=updated_at,
                message_count=len(messages) if isinstance(messages, list) else 0,
            )
            if len(summaries) >= limit:
                break
    except Exception:  # noqa: BLE001 — checkpointer API differences across versions
        logger.exception("farm_agent.list_threads_failed user=%s", user.id)
        return []

    return sorted(
        summaries.values(),
        key=lambda s: s.updated_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


@router.get("/threads/{session_id}", response_model=ThreadDetail)
async def get_thread(
    session_id: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> ThreadDetail:
    """단일 스레드의 사용자/어시스턴트 메시지 복원."""
    agent = _agent(request)
    config = _runtime_config(user.id, session_id)
    try:
        state = await agent.aget_state(config)
    except Exception:
        logger.exception("farm_agent.get_thread_failed user=%s session=%s", user.id, session_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="해당 스레드를 찾을 수 없습니다.",
        )

    values = getattr(state, "values", state)
    raw_messages = (
        values.get("messages", []) if isinstance(values, dict) else []
    )
    out: list[ThreadMessage] = []
    for msg in raw_messages:
        if _is_human_message(msg):
            role = "user"
        elif _is_assistant_message(msg):
            role = "assistant"
        else:
            continue
        content = _content_to_text(getattr(msg, "content", None)).strip()
        if not content:
            continue
        out.append(ThreadMessage(role=role, content=content))
    return ThreadDetail(session_id=session_id, messages=out)


# ── HITL: IoT 제어 승인 ─────────────────────────────────────────────────────


class ApproveActionIn(BaseModel):
    session_id: str = Field(description="에이전트 제안이 발생한 세션 ID")
    control_type: str = Field(description="ventilation | irrigation | lighting | shading")
    action: dict[str, Any] = Field(description="Relay 가 받는 action payload")
    # Optional client-supplied idempotency key. If absent we derive one from
    # (session_id, control_type, action) so a re-click of the same approval
    # button still collides on the same key.
    action_id: str | None = Field(
        default=None,
        description="멱등성 키 — 동일 키로 두 번째 요청은 409 로 거부됨",
        max_length=64,
    )


class ApproveActionOut(BaseModel):
    ok: bool
    relay_status: int | None = None
    detail: str | None = None
    action_id: str | None = None


_ALLOWED_CONTROL_TYPES = {"ventilation", "irrigation", "lighting", "shading"}


@router.post("/approve-action", response_model=ApproveActionOut)
async def approve_action(
    payload: ApproveActionIn,
    user: User = Depends(get_current_user),
) -> ApproveActionOut:
    """사용자가 에이전트의 IoT 제어 제안을 승인한 경우 Relay 로 forward.

    HITL 안전 패턴:
      - 에이전트 자체는 IoT 제어를 직접 호출하지 못한다 (read-only tool 만 보유).
      - 본 엔드포인트만이 실제 Relay POST 를 수행하며, 인증된 사용자가
        명시적으로 호출한 경우에만 동작한다.
      - control_type 화이트리스트로 임의 명령 주입 차단.
    """
    if payload.control_type not in _ALLOWED_CONTROL_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"허용되지 않는 control_type: {payload.control_type}",
        )

    relay_base = (settings.IOT_RELAY_BASE_URL or "").rstrip("/")
    if not relay_base:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IoT Relay 가 설정되지 않았습니다.",
        )

    # Idempotency: derive a stable key when the client doesn't supply one,
    # then claim it in Redis. Re-clicks / SSE replays / network retries that
    # produce the same action collide here and never reach the relay.
    from app.services.farm_agent.locks import (
        claim_actuator_action,
        derive_action_id,
    )

    action_id = payload.action_id or derive_action_id(
        payload.session_id, payload.control_type, payload.action
    )
    claimed = await claim_actuator_action(
        action_id, user_id=user.id, control_type=payload.control_type
    )
    if not claimed:
        logger.info(
            "approve_action.idempotent_replay user=%s control=%s action_id=%s",
            user.id, payload.control_type, action_id,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 처리된 요청입니다 (멱등성). 잠시 후 다시 시도해주세요.",
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                f"{relay_base}/api/v1/control",
                json={
                    "control_type": payload.control_type,
                    "action": payload.action,
                    "source": "agent",
                    "user_id": user.id,
                    "action_id": action_id,  # relay should also dedupe on this
                },
            )
    except httpx.HTTPError as exc:
        logger.warning("approve_action.relay_unreachable user=%s err=%s", user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="IoT Relay 호출에 실패했습니다.",
        )

    detail: str | None = None
    if res.status_code >= 400:
        try:
            detail = (res.json() or {}).get("detail")
        except Exception:  # noqa: BLE001
            detail = res.text[:200]
        return ApproveActionOut(
            ok=False,
            relay_status=res.status_code,
            detail=detail,
            action_id=action_id,
        )

    return ApproveActionOut(
        ok=True, relay_status=res.status_code, action_id=action_id
    )
