"""Fast-path: 단순 질의는 LLM 오케스트레이터를 건너뛰고 도구를 직접 호출.

설계 의도:
  Gemma-4-31B 오케스트레이터는 단순한 "오늘 날씨 어때?" 같은 질문도
  task → 서브에이전트 → 도구 호출 → 합성으로 처리해 LLM 2회 비용이 든다.
  본 모듈은 정규식 매칭으로 명백한 질의를 감지해 도구를 직접 호출하고
  결과를 마크다운으로 포맷해 반환한다.

안전 원칙:
  - **모호하면 fast-path 거부** — 의심되면 None을 반환해 정상 에이전트 흐름으로 위임.
  - 진단·농약·자격 판정 같은 안전 민감 도메인은 절대 fast-path 적용 금지.
  - 모든 패턴은 한국어 사용자 입력 가정.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

from langchain_core.runnables import RunnableConfig

from app.core.config import settings
from app.services.farm_agent.tools import (
    get_current_weather,
    get_journal_daily_summary,
    get_market_prices,
    get_recent_iot_decisions,
)
from app.services.kamis_aliases import matches_kamis_item

logger = logging.getLogger(__name__)

# ── 패턴 정의 ────────────────────────────────────────────────────────────────
# 매우 보수적으로. 매칭이 모호하면 차라리 fast-path 거부.
# 패턴 추가 시 반드시 한국어 농민 사용자가 실제로 입력할 형태인지 검증.

# 한국어 어미 처리 노트:
#   "알려줘" = "알려" + "줘" (보조용언). 정규식 alternation은 토큰 단위가 아니므로
#   전체 입력을 "키워드 + (한글 어미들)*" 형태로 받기 위해 마지막 그룹을
#   [가-힣\s]* 로 두어 어떤 보조용언/조사도 허용한다 (단, 다른 의미 키워드는
#   _BLOCKLIST 가 먼저 차단하므로 안전).

_WEATHER_RE = re.compile(
    r"^\s*(오늘|지금|현재|이번\s*주|내일)?\s*"
    r"(날씨|기온|비|강수|기상|예보)[가-힣\s.?!]*$"
)

_PRICE_RE = re.compile(
    r"^\s*([가-힣]{1,10})\s*(시세|가격|얼마|값)[가-힣\s.?!]*$"
)

# IoT 키워드는 필수 — 과거 `?` 가 붙어 "어제 뭐 했어?" 같은 비-IoT 질의도 매칭하던 오탐 수정
_IOT_RE = re.compile(
    r"^\s*(어제|오늘|최근)\s*(관수|물주기|환기|광|차광|조명|제어)[가-힣\s.?!]*$"
)

_JOURNAL_RE = re.compile(
    r"^\s*(어제|오늘|(\d{4}-\d{2}-\d{2}))\s*(영농일지|일지|작업|기록)[가-힣\s.?!]*$"
)

# fast-path 절대 적용 금지 (안전 민감 키워드)
_BLOCKLIST = re.compile(
    r"(농약|희석|살포|병해|해충|진단|방제|직불|보조금|지원금|자격|시행지침)"
)


# ── 디스패처 ─────────────────────────────────────────────────────────────────


async def try_fast_path(question: str, user_id: str) -> str | None:
    """질문이 fast-path 패턴에 매칭되면 직접 도구를 호출해 답을 반환. 아니면 None.

    Args:
        question: 사용자 질의 (한국어).
        user_id: 로그인 사용자 식별자 (RunnableConfig 주입용).

    Returns:
        - 마크다운 답변 (fast-path 적중)
        - None (정상 에이전트 흐름으로 위임)
    """
    q = (question or "").strip()
    # 너무 길면 복잡한 질의로 간주 — 한도는 settings로 외부화
    if not q or len(q) > settings.FARM_AGENT_FAST_PATH_MAX_LEN:
        return None
    if _BLOCKLIST.search(q):  # 안전 민감 키워드는 무조건 정상 흐름
        return None

    config: RunnableConfig = {"configurable": {"user_id": user_id}}

    try:
        # 1) 날씨
        if _WEATHER_RE.match(q):
            data = json.loads(await get_current_weather.ainvoke({}))
            return _format_weather(data)

        # 2) 시세
        m = _PRICE_RE.match(q)
        if m:
            item_name = m.group(1)
            data = json.loads(
                await get_market_prices.ainvoke({"category_name": ""})
            )
            # FarmOS 재배 용어(예: "벼") ↔ KAMIS 유통 용어(예: "쌀") alias 적용.
            # 매핑 없는 단어는 원래 단어 그대로 substring 매칭(기존 동작).
            matches = [
                it for it in data
                if matches_kamis_item(item_name, it.get("item_name"))
            ][:5]
            if not matches:
                return None  # 매칭 0건 → 에이전트가 더 잘 처리
            return _format_prices(item_name, matches)

        # 3) IoT 이력
        if _IOT_RE.match(q):
            hours = 24 if "오늘" in q or "어제" in q else 72
            data = json.loads(
                await get_recent_iot_decisions.ainvoke({"hours": hours})
            )
            return _format_iot(data, hours)

        # 4) 영농일지 요약
        m = _JOURNAL_RE.match(q)
        if m:
            target = (
                m.group(2) if m.group(2)
                else date.today().isoformat()
            )
            data = json.loads(
                await get_journal_daily_summary.ainvoke(
                    {"target_date": target}, config=config
                )
            )
            return data.get("summary_text") or None

    except Exception as exc:  # noqa: BLE001 — fast-path 실패는 정상 흐름으로 위임
        logger.info("fast_path.miss reason=exception err=%s", exc)
        return None

    return None


# ── 포맷터 (사용자 답변용 한국어 마크다운) ───────────────────────────────────


def _format_weather(data: dict) -> str:
    cur = data.get("current") or {}
    src = data.get("source", "kma")
    parts = [
        "## 🌤️ 현재 날씨",
        f"- 기온: {cur.get('temperature', '?')}℃",
        f"- 습도: {cur.get('humidity', '?')}%",
        f"- 풍속: {cur.get('wind_speed', '?')}m/s",
    ]
    pty = cur.get("precipitation_type")
    if pty and pty != "없음":
        parts.append(f"- 강수: {pty} ({cur.get('precipitation', 0)}mm/h)")
    parts.append(f"\n_데이터 출처: {'기상청' if src == 'kma' else 'mock'}_")

    forecasts = data.get("forecasts") or []
    if forecasts:
        parts.append("\n### 단기 예보")
        for f in forecasts[:3]:
            when = f.get("time") or f"{f.get('hours_ahead', '?')}시간 후"
            if f.get("date"):
                when = f"{f.get('date')} {when}"
            parts.append(
                f"- {when}: "
                f"{f.get('temperature', '?')}℃, "
                f"{f.get('sky', '?')}, 강수확률 {f.get('precipitation_prob', 0)}%"
            )
    return "\n".join(parts)


def _format_prices(query_item: str, items: list[dict]) -> str:
    parts = [f"## 💰 {query_item} 시세 (KAMIS 최신)"]
    for it in items:
        name = it.get("item_name", "?")
        unit = it.get("unit", "")
        price = it.get("dpr1") or it.get("value", "?")
        direction = it.get("direction", "")
        parts.append(f"- {name} ({unit}): {price}원 {direction}")
    return "\n".join(parts)


def _format_iot(data: dict, hours: int) -> str:
    items = data.get("items", [])
    if not items:
        return f"## 🤖 최근 {hours}시간 IoT 제어 이력\n\n기록된 자동 제어가 없습니다."
    parts = [f"## 🤖 최근 {hours}시간 IoT 제어 이력 ({data.get('count', 0)}건)"]
    for r in items[:10]:
        ts = (r.get("timestamp") or "")[:16].replace("T", " ")
        ct = r.get("control_type", "?")
        reason = (r.get("reason") or "")[:50]
        parts.append(f"- {ts} · **{ct}** · {reason}")
    return "\n".join(parts)
