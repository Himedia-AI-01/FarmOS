"""FarmOS Deep Agent — 사용자 대화형 농업 AI 비서.

LangChain Deep Agents (v0.5+) 기반. Gemma 4-31B-IT 오케스트레이터가
3개 서브에이전트(diagnosis / subsidy / farm-data)에 task 도구로 위임한다.

지속성: AsyncPostgresSaver (lifespan에서 풀 주입).
스트리밍: astream(stream_mode="messages") → SSE.
"""

from app.services.farm_agent.agent import build_farm_agent

__all__ = ["build_farm_agent"]
