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

## Snow / 폭설 advisory threshold + fast-path
- status: shipped: a48e513
- area: backend
- why: 시설하우스 적설하중 붕괴, 과수 가지 부러짐, 월동작물 동해는 한국 겨울 농업의 실제 재해. 현재 weather_alerts 는 snow 카테고리가 없어 "내일 폭설?" 단답형 fast-path 도 만들 수 없다.
- slice: weather_alerts.py 에 `_detect_snow` 추가 (daily.sky 가 눈/진눈깨비/눈날림 + precip 임계). snow crop_hint 추가 (시설하우스/사과/배/마늘/양파). fast_path 의 `_GENERAL_RISK_RE` 에 폭설/대설/적설 키워드 + `_RISK_KEYWORD_TO_KINDS` 에 snow 매핑.
- files: backend/app/services/farm_agent/weather_alerts.py (MODIFY), backend/app/services/farm_agent/fast_path.py (MODIFY), backend/tests/test_weather_alerts.py (MODIFY), backend/tests/test_fast_path.py (MODIFY)
- risk: low

## Pest pressure window (병해충 발생 호조 환경)
- status: shipped: 9a50473
- area: backend
- why: 노균/잿빛곰팡이/탄저 외에 진딧물·총채벌레·점박이응애 등은 온도+건조도 패턴이 다르다. 단일 fungal_humidity 임계로는 부족. 병해충 다양화 필요.
- slice: weather_alerts.py 에 pest 카테고리(`mites_window`, `aphid_window`) 추가 — 고온+저습 (응애), 따뜻+건조 (진딧물). fast-path 키워드는 _BLOCKLIST 에 막혀 있어 brief.py / 도구로만 노출.
- files: backend/app/services/farm_agent/weather_alerts.py (MODIFY)
- risk: med (임계 근거 문헌 추가 검증 필요)

## Diurnal temperature swing (일교차) advisory threshold
- status: shipped: e672968
- area: backend
- why: 한국 봄·가을 전이기 일교차 16℃ 초과는 사과·배·포도 열과, 토마토·딸기 갈라짐, 배추 추대 가속, 벼 등숙기 야간저온 등 작물별 직격 신호. 현재 weather_alerts 는 절댓값 임계만 보고 일교차 카테고리가 없어 작은 모델이 직접 (tmax - tmin) 비교를 해야 한다.
- slice: weather_alerts.py 에 `_DIURNAL_RANGE_*` 임계 + 일별 (tmax - tmin) 검사 인라인 추가, ≥16℃ warning / ≥20℃ critical advisory 생성. 8개 작물 temp_swing crop_hint 추가. fast-path 노출은 별도 iter (regex 신영역 위험).
- files: backend/app/services/farm_agent/weather_alerts.py (MODIFY), backend/tests/test_weather_alerts.py (MODIFY)
- risk: low

## Diurnal swing (일교차) fast-path keyword
- status: shipped: 1b607e6
- area: backend
- why: iter-6 가 temp_swing advisory 임계는 추가했지만 "이번주 일교차 어때?" 같은 단답형 질의는 여전히 LLM 라우팅을 거친다. 이미 검증된 `_GENERAL_RISK_RE` + `_RISK_KEYWORD_TO_KINDS` 인프라에 1개 키워드 + 1개 매핑만 추가하면 즉답 가능.
- slice: fast_path.py 의 `_GENERAL_RISK_RE` alternation 에 `일교차` 추가 + `_RISK_KEYWORD_TO_KINDS` 에 `(("일교차",), frozenset({"temp_swing"}), "🌡️ 일교차 위험")` 한 줄. "기온차" 는 _WEATHER_RE 의 "기온" prefix 와 충돌하므로 의도적 제외.
- files: backend/app/services/farm_agent/fast_path.py (MODIFY), backend/tests/test_fast_path.py (MODIFY)
- risk: low

## Cold wave (한파/혹한) explicit advisory kind
- status: shipped: 996353a
- area: backend
- why: 현재 한파/혹한 fast-path 질의는 frost kind 만 반환 → tmin = -10℃ 도 "🔴 서리/동해 가능, 보온·살수 검토" 라는 메시지를 받는다. 강한 한파일 때 "살수" 는 즉시 동결로 역효과 — 농민에게 잘못된 조언. 한파는 frost 와 별개로 "시설 난방·동파·월동 보온재" 가 핵심 액션.
- slice: weather_alerts.py 에 `_COLD_WAVE_*` 임계 + `cold_wave` kind 추가 (tmin ≤ -10℃ warning, ≤ -15℃ critical, KMA 한파특보 -12℃ 기준 보수적 채택). frost 와 동시 발화 허용(서로 다른 액션). 8개 작물 cold_wave crop_hint. fast_path `_RISK_KEYWORD_TO_KINDS` 의 한파/혹한 매핑에 cold_wave 1개 항목만 추가 (frost 유지 — backward compat).
- files: backend/app/services/farm_agent/weather_alerts.py (MODIFY), backend/app/services/farm_agent/fast_path.py (MODIFY), backend/tests/test_weather_alerts.py (MODIFY)
- risk: low

## Hail / 우박 advisory (data-dependent)
- status: new
- area: backend
- why: 우박은 사과·배·포도·시설토마토 한 번에 전손 가능한 재해. KMA 단기예보 sky/PTY 코드는 우박 직접 표기 없음 — derived signal (강한 대류성 호우 + 30℃ 상한 후 급강하 + 풍속 급증) 또는 별도 KMA 우박특보 API 필요. 데이터 가용성 검증 필수.
- slice: 1단계는 weather_client.py 에 우박특보 필드가 있는지 검증 → 있으면 weather_alerts.py 에 hail kind, 없으면 데이터 차원 제안서로 grades.
- files: backend/app/core/weather_client.py (READ), backend/app/services/farm_agent/weather_alerts.py (MODIFY if data exists)
- risk: med (데이터 차원 의존)

## Wind chill (체감온도) advisory for outdoor work scheduling
- status: shipped: 6a402ee
- area: backend
- why: 한국 겨울 농민 야외작업(전정·수확·시설 점검) 안전성은 절대온도가 아닌 풍속 보정 체감온도가 결정. 절대 0℃ + 풍속 10m/s = 체감 -10℃ 수준. 현재 frost/cold_wave 는 절대온도만 본다.
- slice: weather_alerts.py 에 `_compute_wind_chill(t, v)` (KMA/Environment Canada 공식) + `wind_chill` kind. 임계: 체감 ≤ -10℃ warning, ≤ -20℃ critical. wmax 와 tmin 결합.
- files: backend/app/services/farm_agent/weather_alerts.py (MODIFY)
- risk: low

## Calm spray window (방제 호조 시간대)
- status: new
- area: backend
- why: 현재 모든 advisory 는 "위험 신호". 농민이 정말 필요한 정보는 종종 "내일 약 칠 수 있나?" — 풍속 < 3m/s + 강수확률 < 30% + 기온 15-28℃ 인 시간대 affirmative 추천. 약제 살포 효율과 안전성에 직결.
- slice: weather_alerts.py 또는 신 `spray_window.py` 에 hourly/3h forecast 스캔, 조건 만족 슬롯 list 반환. 1차는 advisory list 외부에 분리 도구 (briefing 직접 호출).
- files: backend/app/services/farm_agent/weather_alerts.py 또는 backend/app/services/farm_agent/spray_window.py (NEW), backend/app/services/farm_agent/tools.py (MODIFY)
- risk: med (예보 시간 해상도 의존)

## Tropical night (열대야) advisory kind
- status: shipped: e78e375
- area: backend
- why: 현재 heatwave 는 일 최고기온(tmax) 만 본다. 일 최저(tmin) ≥ 25℃ 인 열대야는 별개 농업 재해 — 벼 등숙기 야간 호흡 증가로 등숙률·식미 저하, 사과·배 야간저온 부족으로 착색 부진, 토마토·고추 야간고온으로 수정 불량·낙화. 액션도 다름 (낮 차광 vs 야간 환기·관수 사이클 조정). frost vs cold_wave 가 분리됐듯 heatwave vs tropical_night 도 분리되어야 한다.
- slice: weather_alerts.py 에 `_TROPICAL_NIGHT_*` 임계 + `tropical_night` kind 추가 — tmin ≥ 25℃ warning, tmin ≥ 27℃ critical (보수적; KMA 초열대야 30℃ 보다 낮춤). heatwave 와 동시 발화 허용. 7~8개 작물 tropical_night crop_hint. fast-path 키워드는 본 iter 범위 밖 (별도 iter).
- files: backend/app/services/farm_agent/weather_alerts.py (MODIFY), backend/tests/test_weather_alerts.py (MODIFY)
- risk: low

## Tropical night (열대야) fast-path keyword
- status: shipped: 2b868f8
- area: backend
- why: iter-11 이 tropical_night advisory 임계는 추가했지만 "오늘 열대야?" / "내일 초열대야 와?" 같은 단답형 질의는 여전히 LLM 라우팅을 거친다. 이미 검증된 `_GENERAL_RISK_RE` + `_RISK_KEYWORD_TO_KINDS` 인프라(iter-3,5,7,8) 에 키워드 + 매핑만 추가하면 즉답 가능. 한여름 야간 환기·미스트 의사결정은 시간이 곧 작물 손실이라 LLM 지연을 줄이는 가치가 크다.
- slice: fast_path.py 의 `_GENERAL_RISK_RE` alternation 에 `열대야|초열대야` 추가 + `_RISK_KEYWORD_TO_KINDS` 에 `(("열대야", "초열대야"), frozenset({"tropical_night"}), "🌙 열대야 (야간 고온) 위험")` 한 줄. 단독 "야간" 은 야외/실내·작업·온도 등 의미 모호로 의도적 제외. heatwave 와 동시 노출 안 함 (질의 의도가 야간 한정이면 야간 advisory 만).
- files: backend/app/services/farm_agent/fast_path.py (MODIFY), backend/tests/test_fast_path.py (MODIFY)
- risk: low

<!-- ── Frontend pivot (iter-13+) — UI/UX must be primary surface ── -->

## Market price quick search + `/` keyboard shortcut
- status: shipped: 863af7d
- area: frontend
- why: 시세 표는 부류 토글만 있어 "오이가 얼마지?" 류를 손가락으로 한참 스크롤해야 한다. 텍스트 검색 + `/` 포커스 단축키 + 검색 결과 카운트(aria-live) 만 더해도 농민이 표를 즉시 찾는다.
- slice: MarketPricePage.tsx 에 검색 input 추가, item_name/category_name 필터, "/" 키로 input focus, ESC 로 검색 초기화, aria-live 로 결과 N건 음성 안내, 빈 상태 카드.
- files: frontend/src/modules/market/MarketPricePage.tsx (MODIFY)
- risk: low

## Sortable column headers in price table
- status: shipped: e26d61b
- area: frontend
- why: 표 정렬은 현재 백엔드 응답 순서(부류 코드) 만. 농민은 "당일 가격 높은 순", "최근 변동 큰 순" 으로 보고 싶어 한다. 헤더 클릭으로 정렬 토글.
- slice: MarketPricePage 에 sort key state(name/current/change), 헤더에 ▲▼ 표시 + 클릭 토글, useMemo 비교자 분기.
- files: frontend/src/modules/market/MarketPricePage.tsx (MODIFY)
- risk: low

## prefers-reduced-motion respect (global)
- status: shipped: 1bd7bf4
- area: frontend
- why: framer-motion + Tailwind animate-* 애니메이션이 다수 페이지에서 자동 재생. 전정·운전 직후 진입 등 멀미 유발 가능성. WCAG 2.1 SC 2.3.3.
- slice: index.css 에 `@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation-duration: .01ms !important; transition-duration: .01ms !important; scroll-behavior: auto !important; } }` 글로벌 가드 + framer-motion `MotionConfig` 으로 `reducedMotion="user"` 글로벌 설정.
- files: frontend/src/index.css (MODIFY), frontend/src/App.tsx (would touch — but App.tsx is in-progress; SKIP MotionConfig path or use main.tsx instead)
- risk: low

## Diagnosis page drag-and-drop image upload
- status: new
- area: frontend
- why: 모바일 카메라는 이미 잘 되지만, PC 사용 시(데스크톱 행정 직원) 파일 선택 다이얼로그뿐이라 이질감. react-dropzone 이 의존성에 이미 있고 PROMPT.md 도 명시함. 드롭존 + 멀티이미지 미리보기 + 제거 버튼.
- slice: DiagnosisPage 또는 모듈 내 신 `DropzoneUploader.tsx` 컴포넌트, useDropzone(accept image/*, multiple), 미리보기 thumbnail grid, 파일 제거.
- files: frontend/src/modules/diagnosis/DropzoneUploader.tsx (NEW), frontend/src/modules/diagnosis/DiagnosisPage.tsx (MODIFY)
- risk: med (기존 업로드 플로우와 충돌 가능 — 신규 컴포넌트로 분리 권장)

## Skeleton loaders for Market price table
- status: shipped: cd8612b
- area: frontend
- why: 현재 시세 페이지 초기 로딩은 회색 스피너 1개("시세 정보를 불러오는 중...") — UX 가 평이. tailwind animate-pulse 로 표/카드 shape skeleton 을 표시하면 인지 지연이 줄어든다.
- slice: MarketPricePage.tsx 의 loading 분기를 skeleton table(8 rows × 7 cols, animate-pulse bg-gray-100) + 변동 카드 placeholder 3개로 교체.
- files: frontend/src/modules/market/MarketPricePage.tsx (MODIFY), frontend/src/modules/market/MarketPriceSkeleton.tsx (NEW)
- risk: low

## Weather page skeleton loaders
- status: shipped: 394793f
- area: frontend
- why: WeatherPage 의 첫 진입은 헤더 카드 안에 `-°C` / `-%` 등 마이너스 자리 표시만 떠 있어 인지 지연 + UX 빈약. 5일 예보·시간별 예보 그리드는 isLoading 분기에서 빈 카드 5/4 개를 렌더만 한다. animate-pulse shape skeleton 으로 교체 — iter-15 의 MarketPriceSkeleton 패턴을 그대로 재사용. 농민이 매일 가장 많이 보는 페이지인 만큼 우선순위 높다.
- slice: 신규 `WeatherSkeleton.tsx` — 헤더 카드(현재 기온 + 4 메트릭 grid) + 5일 예보 grid + 시간별 4 카드 + 작업 판단 3 카드 placeholder, 모두 animate-pulse bg-gray-100. WeatherPage 의 첫 로딩(`!weather && isLoading`) 분기에서 사용. 데이터 도착 후 부분 갱신은 skeleton 이 아닌 기존 인플레이스 분기 유지. SR 용 "불러오는 중" status + 시각 데코는 aria-hidden.
- files: frontend/src/modules/weather/WeatherPage.tsx (MODIFY), frontend/src/modules/weather/WeatherSkeleton.tsx (NEW)
- risk: low

## Weather page refresh — keyboard shortcut + relative timestamp
- status: shipped: c66bda8
- area: frontend
- why: 농민이 외부에서 돌아와 페이지 리프레시할 때 우상단 버튼을 손가락으로 찾아야 한다. R 단축키 + "방금 전 / 3분 전" 상대 시간 표시는 작은 변경으로 큰 가독성 개선.
- slice: WeatherPage 에 keydown 리스너로 `r` 키(IME / 입력 요소 가드) 매핑, generated_at 을 1분 인터벌로 상대 시간(`방금 전`/`N분 전`/`N시간 전`) 변환해 헤더에 표기.
- files: frontend/src/modules/weather/WeatherPage.tsx (MODIFY)
- risk: low

## Subsidy page initial loader → skeleton card
- status: shipped: 98527c4
- area: frontend
- why: SubsidyPage 의 초기 로딩이 평이한 회색 텍스트 한 줄. 농민이 직불사업 자격 확인을 시작하는 첫인상 — shape skeleton 으로 헤더/요약 타일/결과 카드 3-5개 placeholder 보여주면 체감 로딩 절반.
- slice: 신규 `SubsidySkeleton.tsx` 또는 인라인 — 헤더 카드 + 3 SummaryTile + 결과 그리드 4 카드 placeholder. animate-pulse + aria-hidden + sr-only status.
- files: frontend/src/modules/subsidy/SubsidyPage.tsx (MODIFY), frontend/src/modules/subsidy/SubsidySkeleton.tsx (NEW)
- risk: low

## Agent console "copy to clipboard" on agent messages
- status: new
- area: frontend
- why: 농민이 농업기술센터/조합 직원에게 농약·시세 응답을 복사해 보내고 싶을 때 마우스 드래그 + Ctrl+C 가 모바일에서 어렵다. 메시지 카드 호버 / 모바일 길게 누름 시 노출되는 1-tap 복사 버튼.
- slice: AgentMarkdown.tsx 또는 FarmAgentConsole.tsx 의 어시스턴트 메시지 wrapper 우상단에 MdContentCopy 버튼, navigator.clipboard.writeText, 성공 시 react-hot-toast 알림.
- files: frontend/src/components/agent/FarmAgentConsole.tsx (MODIFY) 또는 frontend/src/components/agent/AgentMarkdown.tsx (MODIFY)
- risk: med (이미 dirty 상태인 파일들 — 본 iter 외 수정과 충돌 가능)

## Journal page skeleton loaders
- status: shipped: 12d586a
- area: frontend
- why: JournalPage 의 초기 로딩은 `<div>불러오는 중...</div>` 단일 회색 텍스트뿐이다. 농민이 매일 영농일지를 확인·작성하는 메인 surface 인 만큼 iter-15(Market)/17(Weather)/19(Subsidy) 의 검증된 shape skeleton 패턴을 동일하게 적용해 인지 지연을 줄인다.
- slice: 신규 `JournalSkeleton.tsx` — 타임라인 형태(2개 날짜 그룹 × 3 카드 placeholder), 모두 animate-pulse 회색 블록. JournalPage 의 `{loading && ...}` 분기에서 텍스트 한 줄을 컴포넌트로 교체. SR 사용자에겐 sr-only role="status" 안내, 시각 데코는 aria-hidden. 액션 바·DailyJournalPanel·MissingFieldsAlert·필터 pill 은 이미 자체 렌더되므로 timeline 영역만 교체해 깜빡임 최소화.
- files: frontend/src/modules/journal/JournalPage.tsx (MODIFY), frontend/src/modules/journal/JournalSkeleton.tsx (NEW)
- risk: low

## Diagnosis page recent-history skeleton loader (load vs empty disambiguation)
- status: in-progress
- area: frontend
- why: DiagnosisPage 의 "최근 진단 기록" 섹션은 `history.length === 0` 일 때 곧바로 "최근 진단 내역이 없습니다." 빈 상태 카드를 보여준다 — 그러나 fetchHistory 가 아직 응답을 받지 못한 초기 진입 시에도 동일 문구가 노출돼 농민이 "정말 기록이 없는지 / 아직 로딩 중인지" 구분 불가. 단순한 skeleton 추가로 (a) 인지 지연 감소 (b) 빈 상태 의미 정확화 두 효과 동시 확보.
- slice: 신규 `DiagnosisHistorySkeleton.tsx` (~50줄) — 실제 history 카드 모양(체크박스+badge+title+meta+삭제+chat 버튼)을 그대로 모방한 4개 placeholder, animate-pulse 회색 블록. DiagnosisPage 에 `loadingHistory` state(initial true) 추가, fetchHistory 시작/finally 토글, useEffect 의 user 없음 분기에서는 false 세팅. 렌더 분기: `loadingHistory && history.length === 0` → skeleton, `!loadingHistory && length === 0` → 기존 빈 상태, 그 외 → 기존 list. SR 사용자에겐 sr-only role="status" + aria-live=polite 안내, 시각 데코는 컨테이너 aria-hidden. iter-15/17/19/20 패턴 일관 적용.
- files: frontend/src/modules/diagnosis/DiagnosisPage.tsx (MODIFY), frontend/src/modules/diagnosis/DiagnosisHistorySkeleton.tsx (NEW)
- risk: low
