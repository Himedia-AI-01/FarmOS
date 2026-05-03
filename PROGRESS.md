# FarmOS Ralph Progress Log

| Iter | Date | Area | Title | Commit | Outcome |
|------|------|------|-------|--------|---------|
| 1 | 2026-05-04 | backend | Crop-aware weather risk advisory tool | f6435a7 | New deterministic analyzer + `get_weather_risk_advisory` tool wired into FARM_DATA + ORCHESTRATOR. 13 unit tests pass; backend imports clean; frontend build clean. |
| 2 | 2026-05-04 | backend | Frost-shield fast-path pattern | 96a5575 | Added `_FROST_RE` + `_format_frost` to fast_path.py; "오늘 밤 서리?" 류 질의를 LLM 없이 weather_alerts 의 frost 어드바이저리만 추려 응답. 9 신규 + 13 기존 단위 테스트 통과; 백엔드/프론트엔드 빌드 통과. |
| 3 | 2026-05-04 | backend | General extreme-weather risk fast-path | 15c54c7 | `_GENERAL_RISK_RE` + `_format_general_risks` + `_select_risk_kinds` 로 폭염/호우/강풍/한파/특보 단답형 질의를 weather_alerts engine 으로 즉답. 키워드별 advisory kind 화이트리스트 필터링; fallback 경로는 critical+warning 만 노출해 info 잡음 차단. 13 신규 + 23 기존 = 36/36 단위 테스트 통과; 백엔드 import + 프론트엔드 build clean. |
| 4 | 2026-05-04 | backend | Drought advisory threshold | 3a47e2b | `_detect_drought` + `drought` kind 추가 — 향후 daily_forecasts 첫 항목부터 강수<1mm 연속일을 세어 5일 warning / 7일 critical advisory 생성. 결측·강우·현재 강수 발생 시 보수적 중단. 10개 작물 drought crop_hint 추가, sort key 도 "향후 Nd" 슬롯 부여. 8 신규 + 13 기존 = 21/21 + fast_path 23/23 단위 테스트 통과; 백엔드 import + 프론트엔드 build clean. |
