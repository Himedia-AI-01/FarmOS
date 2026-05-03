# FarmOS Ralph Backlog

Format per item:
```
## <title>
- status: [new|in-progress|shipped: <sha>|blocked: <reason>]
- area: backend|frontend|fullstack
- why: <1 line — value for FarmOS users>
- slice: <smallest shippable change>
- files: <expected paths>
- risk: low|med|high
```

<!-- Ralph appends new ideas below this line each iteration. -->

## Crop-aware weather risk advisory tool
- status: shipped: f6435a7
- area: backend
- why: Korean farmers ask "내일 비 와도 괜찮을까?" — turn KMA forecast into actionable, crop-specific risk flags (frost / heatwave / strong wind / heavy rain / fungal humidity window). Deterministic, no LLM, immediately reusable in briefings + Q&A.
- slice: pure-Python analyzer over existing `get_weather()` payload + a thin `@tool` wrapper exposed to FARM_DATA + ORCHESTRATOR. No new external API. Tests run without DB or network.
- files: backend/app/services/farm_agent/weather_alerts.py (NEW), backend/app/services/farm_agent/tools.py (MODIFY), backend/tests/__init__.py (NEW), backend/tests/test_weather_alerts.py (NEW)
- risk: low

## Frost-shield fast-path pattern
- status: shipped: 96a5575
- area: backend
- why: "오늘 밤 서리?" / "내일 새벽 영하?" 류 단답형 질의를 LLM 없이 확정 응답. 사장님이 야간 작업 결정에 즉시 사용.
- slice: fast_path.py 에 `_FROST_RE` 패턴 추가 + weather_alerts 호출해 향후 24h temp_min 기반 동해 위험만 단출하게 마크다운 반환.
- files: backend/app/services/farm_agent/fast_path.py (MODIFY)
- risk: low

## Daily briefing risk integration
- status: new
- area: backend
- why: 현재 브리핑은 LLM 이 텍스트 임계 비교(강수확률, 풍속 등) 를 직접 한다 — 작은 모델은 종종 임계를 잘못 본다. 결정론적 risk advisory 출력을 prompt 컨텍스트로 주입해 환각 차단.
- slice: briefing.py 의 _BRIEFING_PROMPT 빌드 직전에 weather_alerts 결과를 마크다운 표로 변환해 prompt 내 `## 관측 위험` 섹션으로 삽입.
- files: backend/app/services/farm_agent/briefing.py (MODIFY)
- risk: med (브리핑 prompt 변경은 회귀 영향 큼 — 별도 iter 권장)

## Voice command "긴급 조치" 핸드오프
- status: new
- area: backend
- why: 음성으로 "환기 켜줘" 같은 IoT 직접 조작 발화 시 현재는 LLM 라우팅을 거쳐 수 초 지연. 키워드 화이트리스트로 STT 결과 즉시 HITL approval 카드로 매핑.
- slice: api/farm_agent.py /voice 엔드포인트에서 transcript 가 IoT 동사 + 객체 패턴이면 LLM 호출 생략, 바로 action proposal 응답.
- files: backend/app/api/farm_agent.py (MODIFY)
- risk: med (오인식 발화로 인한 잘못된 IoT 조작 가능성)

## ReasoningBank — 직불 조항 엔트로피 캐시
- status: blocked: already implemented (lru_cache(maxsize=256) on _search_subsidy_regulations_cached + _search_subsidy_regulations_fast_cached at subsidy/tools.py L119-137)
- area: backend
- why: 같은 사용자/유사 질의가 반복될 때 search_subsidy_regulations 의 결과(citation 셋) 를 sha-key 캐시로 묶어 LLM 재구성 비용 절감. AGENTS.md 의 STRATEGIES.md 와 자연스럽게 연동.
- slice: subsidy/tools.py 의 fast 검색 함수에 in-memory LRU(query_norm → top-k citations) 추가 + TTL 1h.
- files: backend/app/services/subsidy/tools.py (MODIFY)
- risk: low

## General weather-risk fast-path (폭염 / 호우 / 강풍 / 한파)
- status: shipped: 15c54c7
- area: backend
- why: 서리/동해는 이미 fast-path 화 되어 있지만 폭염, 호우/폭우, 강풍/돌풍, 한파/혹한 단답형 질의("이번주 폭염?", "내일 폭우?", "오늘 강풍?")는 여전히 LLM 라우팅 비용을 부담. 동일한 deterministic engine 으로 즉답.
- slice: fast_path.py 에 `_GENERAL_RISK_RE` + `_format_general_risks` 추가, dispatcher 분기 한 줄. 질의 키워드별 advisory kind 화이트리스트 필터링.
- files: backend/app/services/farm_agent/fast_path.py (MODIFY), backend/tests/test_fast_path.py (MODIFY)
- risk: low

## Drought / 가뭄 advisory threshold
- status: shipped: 3a47e2b
- area: backend
- why: 현재 weather_alerts 는 가뭄(연속 무강수일, 토양 수분) 카테고리가 없다. 가뭄 관련 fast-path 가 추가되더라도 advisory 를 만들 수 없으면 항상 빈 응답.
- slice: weather_alerts.py 에 `_DROUGHT_DRY_DAYS` 추가, daily_forecasts 의 강수 < 1mm 연속 일수를 세어 advisory 생성. 작물별 가뭄 hint.
- files: backend/app/services/farm_agent/weather_alerts.py (MODIFY), backend/tests/test_weather_alerts.py (MODIFY)
- risk: low

## Daily briefing risk-table injection
- status: new
- area: backend
- why: 브리핑 prompt 가 직접 임계 비교를 하지 않고 결정론적 risk advisory 표를 주입받게 해서 작은 모델의 임계 환각을 차단.
- slice: briefing.py 의 prompt 빌드 직전 `format_advisories_markdown` 결과를 `## 관측 위험` 섹션으로 prepend. 위험 0건이면 섹션 자체 생략.
- files: backend/app/services/farm_agent/briefing.py (MODIFY)
- risk: med (briefing prompt 회귀 영향)
