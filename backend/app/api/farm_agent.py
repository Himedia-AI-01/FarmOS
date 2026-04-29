"""Farm Agent 대화 API.

POST /farm-agent/ask     — 일회성 응답 (JSON, 비스트림 클라이언트용)
POST /farm-agent/stream  — SSE 토큰 스트림 (실시간 채팅 UI용)

세션 영속성:
  - thread_id = `f"{user_id}:{session_id}"`
  - AsyncPostgresSaver가 lifespan에서 주입한 checkpointer로 thread별 상태 저장
"""

from __future__ import annotations

import io
import logging
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field
from PIL import Image
from sse_starlette.sse import EventSourceResponse

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.stt import transcribe_audio
from app.models.user import User
from app.services.farm_agent.briefing import get_or_generate_briefing
from app.services.farm_agent.fast_path import try_fast_path
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
    """RunnableConfig — checkpointer thread + 도구 런타임 의존성 주입."""
    return {
        "configurable": {
            "thread_id": f"{user_id}:{session_id}",
            "user_id": user_id,
        }
    }


def _content_to_text(content: Any) -> str:
    """Normalize LangChain/OpenAI content shapes into displayable text.

    Chat chunks are usually strings, but tool-heavy Deep Agent paths can store
    final content as provider-specific block lists. The frontend only wants
    human-readable assistant text, not tool-call metadata.
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
            if block_type in {None, "text", "output_text"}:
                text = item.get("text") or item.get("content") or item.get("value")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


def _latest_assistant_text_from_state(state: Any) -> str:
    """Return the latest non-empty AI/assistant message text from a graph state."""
    if state is None:
        return ""
    values = getattr(state, "values", state)
    if not isinstance(values, dict):
        return ""
    messages = values.get("messages", [])
    for message in reversed(messages):
        message_type = getattr(message, "type", None)
        role = getattr(message, "role", None)
        if message_type in {"human", "tool", "system"} or role in {"user", "tool", "system"}:
            continue
        text = _content_to_text(getattr(message, "content", None)).strip()
        if text:
            return text
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
            return AskOut(answer=fast_answer, session_id=session_id, fast_path=True)

    # 2) 정상 Deep Agent 흐름
    agent = _agent(request)
    config = _runtime_config(user.id, session_id)

    try:
        state = await agent.ainvoke(
            {"messages": [{"role": "user", "content": payload.question}]},
            config=config,
        )
    except Exception:
        logger.exception("farm_agent.ask 실패 user=%s session=%s", user.id, session_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="에이전트 응답 생성 중 오류가 발생했습니다.",
        )

    answer = _latest_assistant_text_from_state(state)
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

    agent = _agent(request)
    config = _runtime_config(user.id, session_id)

    async def gen():
        import asyncio as _asyncio
        yield {"event": "session", "data": session_id}
        emitted_tool_call_ids: set[str] = set()
        # heartbeat: 토큰 사이의 idle 시간이 길면 SSE 프록시(nginx 등)가 끊는다.
        # FARM_AGENT_SSE_HEARTBEAT_SEC 마다 ping 이벤트를 송출해 keep-alive 유지.
        heartbeat_interval = settings.FARM_AGENT_SSE_HEARTBEAT_SEC
        emitted_text = ""
        stream_failed = False
        try:
            stream = agent.astream(
                {"messages": [{"role": "user", "content": payload.question}]},
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
                                else:
                                    tc_id = getattr(tc, "id", "") or ""
                                    tc_name = getattr(tc, "name", "") or ""
                                dedupe_key = tc_id or f"{tc_name}:{len(emitted_tool_call_ids)}"
                                if tc_name and dedupe_key not in emitted_tool_call_ids:
                                    emitted_tool_call_ids.add(dedupe_key)
                                    yield {"event": "tool", "data": tc_name}

                        if not _is_assistant_message(message):
                            continue
                        text = _content_to_text(getattr(message, "content", None))
                        if text:
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

        try:
            # Recovery path: runs whether the stream finished cleanly with no
            # text emitted, OR crashed partway. LangGraph persists node outputs
            # incrementally, so the LLM's final answer is often already in the
            # checkpointer even when the live stream broke.
            if not emitted_text.strip():
                recovered = await _fetch_state_answer(
                    agent, config,
                    user_id=user.id,
                    session_id=session_id,
                    context="after_failure" if stream_failed else "after_clean_exit",
                )
                if recovered:
                    yield {"event": "token", "data": recovered}
                    emitted_text = recovered

            emit_error, warning_text = _decide_after_recovery(
                stream_failed=stream_failed,
                has_emitted_text=bool(emitted_text.strip()),
            )
            if warning_text:
                yield {"event": "warning", "data": warning_text}
            if emit_error:
                # 보안: 내부 에러 메시지를 그대로 노출하지 않음 (DB 컬럼·내부 호스트 누설 방지)
                yield {
                    "event": "error",
                    "data": "내부 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                }
        finally:
            # 클라이언트가 done 이벤트를 기다리고 있으므로 예외 여부와 무관하게 반드시 종료 신호 emit.
            yield {"event": "done", "data": ""}

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
    return BriefingOut(date=today.isoformat(), content=content, cached=cached)


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
    try:
        state = await agent.ainvoke(
            {"messages": [{"role": "user", "content": transcript}]},
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
