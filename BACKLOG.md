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
- status: in-progress
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
- status: new
- area: backend
- why: 같은 사용자/유사 질의가 반복될 때 search_subsidy_regulations 의 결과(citation 셋) 를 sha-key 캐시로 묶어 LLM 재구성 비용 절감. AGENTS.md 의 STRATEGIES.md 와 자연스럽게 연동.
- slice: subsidy/tools.py 의 fast 검색 함수에 in-memory LRU(query_norm → top-k citations) 추가 + TTL 1h.
- files: backend/app/services/subsidy/tools.py (MODIFY)
- risk: low
