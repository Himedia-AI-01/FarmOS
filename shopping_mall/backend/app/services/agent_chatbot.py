"""에이전트 기반 챗봇 서비스 — ChatbotService와 동일한 인터페이스."""
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from ai.agent import AgentExecutor

if TYPE_CHECKING:
    from ai.agent import ToolMetricData

logger = logging.getLogger(__name__)

HISTORY_WINDOW_SIZE = 6


class AgentChatbotService:
    """AgentExecutor를 래핑하여 기존 ChatbotService.answer() 인터페이스를 구현."""

    def __init__(self, executor: AgentExecutor, system_prompt: str):
        self.executor = executor
        self.system_prompt = system_prompt

    async def answer(
        self,
        db: Session,
        question: str,
        user_id: int | None = None,
        history: list | None = None,
        session_id: int | None = None,
    ) -> dict:
        # history → LLM 메시지 형식 변환 (최근 N턴)
        messages = self._build_history(history)

        # 요청 컨텍스트 생성 (날짜/시각, 로그인 상태)
        from ai.agent import RequestContext
        context = RequestContext.build(user_id)

        # 에이전트 실행
        result = await self.executor.run(
            db=db,
            user_message=question,
            user_id=user_id,
            session_id=session_id,
            history=messages,
            system=self.system_prompt,
            context=context,
        )

        # ChatLog 저장 + 세션 메타데이터 갱신 (단일 트랜잭션)
        from app.models.chat_log import ChatLog
        log = ChatLog(
            user_id=user_id,
            session_id=session_id,
            intent=result.intent,
            question=question,
            answer=result.answer,
            escalated=result.escalated,
        )
        db.add(log)

        if session_id:
            from app.models.chat_session import ChatSession
            session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if session:
                if not session.title:
                    session.title = question[:50]
                session.updated_at = datetime.now(timezone.utc)

        db.commit()

        # 메트릭 저장 (ChatLog commit 이후 별도 트랜잭션)
        if result.metrics:
            self._save_metrics(db, result.metrics, log.id, session_id)

        return {
            "answer": result.answer,
            "intent": result.intent,
            "escalated": result.escalated,
            "trace": result.trace,
        }

    def _save_metrics(
        self,
        db: Session,
        metrics: "list[ToolMetricData]",
        chat_log_id: int | None,
        session_id: int | None,
    ) -> None:
        """도구 메트릭을 DB에 저장. 실패해도 응답에 영향 없음."""
        try:
            from app.models.tool_metric import ToolMetric

            db.add_all([
                ToolMetric(
                    chat_log_id=chat_log_id,
                    session_id=session_id,
                    tool_name=m.tool_name,
                    intent=m.intent,
                    success=m.success,
                    latency_ms=m.latency_ms,
                    empty_result=m.empty_result,
                    iteration=m.iteration,
                )
                for m in metrics
            ])
            db.commit()
        except Exception as e:
            logger.warning("도구 메트릭 저장 실패: %s", e)
            db.rollback()

    # 프론트엔드 role → LLM role 매핑 ("bot" → "assistant")
    _ROLE_MAP: dict[str, str] = {
        "user": "user",
        "assistant": "assistant",
        "bot": "assistant",  # 프론트엔드가 bot으로 전송
    }

    def _build_history(self, history: list | None) -> list[dict]:
        """기존 history 형식 → LLM 메시지 형식 변환."""
        if not history:
            return []

        messages = []
        for item in history[-HISTORY_WINDOW_SIZE:]:
            raw_role = item.get("role", "user")
            role = self._ROLE_MAP.get(raw_role)
            if not role:
                logger.debug("알 수 없는 history role 무시: %s", raw_role)
                continue
            content = item.get("content") or item.get("text", "")
            if content:
                messages.append({"role": role, "content": content})

        return messages
