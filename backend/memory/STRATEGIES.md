# FarmOS Agent — Reasoning Strategies (ReasoningBank-style)

이 파일은 **전략-수준** 추론 힌트와 실패-모드 회피 패턴을 담는다.
구체적 사실은 `AGENTS.md` 에, 도구 사용법은 각 도구 docstring 에. 여기에는
"언제 / 어떻게 / 무엇을 피할지" 만 기록한다.

각 항목 형식:
- **When**: 패턴이 적용되는 상황
- **Strategy**: 취할 행동
- **Pitfall**: 알려진 실패 모드 (있을 때만)

운영자가 직접 추가하거나, 에이전트가 `edit_file` 로 새 패턴을 append 할 수
있다 (자기 개선 루프). 추가 시 동일한 3-필드 포맷을 지키고, 출처 (날짜·세션
ID·평가 결과) 를 한 줄 메모로 남긴다.

---

## R1. 단일-도메인 질의 라우팅
- **When**: 사용자 질문에 단일 도메인 키워드만 등장 (예: "직불금", "병해충",
  "오늘 날씨").
- **Strategy**: 오케스트레이터에서 즉시 해당 서브에이전트로 `task` 위임.
  중간 검토 단계 생략.
- **Pitfall**: 키워드가 둘 이상이면 (예: "직불금 자격 + 처벌") 단일 위임 금지.
  병렬 `tool_calls` 로 라우팅한다.

## R2. 다중-주제 분해
- **When**: 한 질문에 명백히 분리 가능한 하위 질문 두 개 이상.
- **Strategy**: `parallel_tool_calls=True` 활용. 한 라운드에 여러 `task` /
  검색 호출 발행 → LangGraph 병렬 엣지가 동시 실행.
- **Pitfall**: 하위 질문이 의존 관계 (A 결과가 B 입력) 면 병렬 금지. 직렬 실행.

## R3. 직불금 의무 yes/no
- **When**: "꼭 해야 해?", "필수야?", "안 하면 어떻게 돼?" 류의 의무 판정
  질문.
- **Strategy**: `search_subsidy_obligation_check` 사용. 의무·선택 가설을
  병렬로 검색해 유사도 비교로 verdict_hint 도출 — 프롬프트 의존도 최소화.
- **Pitfall**: 일반 검색만 돌리면 부정 사례 (선택사항 명시) 를 놓친다.
  의무 판정에는 반드시 hypothesis-driven 도구.

## R4. 농약·희석배수 안전 응답
- **When**: 진단 결과로 농약·희석배수·살포시기 정보를 사용자에게 보내기 직전.
- **Strategy**: deterministic safety gate (`diagnose_pest` 내 검증) 통과 후
  발송. `verifier-agent` 는 백그라운드 비동기로 cross-check (사용자 응답
  차단 X).
- **Pitfall**: LLM 자체 생성 농약 권고는 절대 사용 금지. 반드시 RDA/NCPMS
  도구 결과만 인용.

## R5. 시세 조회 별칭
- **When**: 사용자가 재배 명칭 (예: "방울토마토") 으로 시세 질문.
- **Strategy**: `get_market_prices_for_crop` 의 재배→유통 alias 매핑 사용.
- **Pitfall**: 직접 `get_market_prices` 에 재배명 전달하면 KAMIS 가 빈 결과
  반환. alias 우선.

## R6. 인용 보존 (긴 컨텍스트)
- **When**: 직불금 시행지침 검색 결과를 답변에 포함할 때.
- **Strategy**: `[doc > 제N조]` 형태 인용 태그 그대로 보존. SummarizationMiddleware
  는 Grok 프로파일에서 제외되어 있음 — 인용 잘리지 않음.
- **Pitfall**: 자체 paraphrase 로 인용 대체 금지. 사용자가 시행지침 원문을
  찾을 수 없게 됨.

## R7. Grok 다중-턴 앵커링 회피
- **When**: 같은 세션에서 두 번째 이상 질문 처리.
- **Strategy**: 현재 질문에만 답변. 직전 턴 paraphrase 금지 (HarnessProfile
  suffix 가 강제하지만 전략 레벨에서도 명시).
- **Pitfall**: Grok 4.1 Fast 는 이전 턴 답변을 다시 출력하는 경향이 있음.
  reasoning OFF 기본값 유지.

## R8. 음성 입력 오타 보정
- **When**: 사용자 입력에 발음 변이로 인한 오타 의심 (예: "고추잎이 검음" →
  "검음" vs "검댐").
- **Strategy**: 핵심 명사 (작물·증상) 위주로 의도 파악. 모호하면 "혹시
  ~말씀이실까요?" 로 1회 확인.
- **Pitfall**: 사용자 50-70 대 다수, 발음 변이 빈번. 표면 문자열 그대로
  검색하면 hit rate 낮음.

## R10. 도구 verdict UNCLEAR 신호 명시 전달
- **When**: 도구 응답에 `verdict_hint` (또는 동등한 confidence/uncertainty 필드) 가
  포함되고 그 값이 `UNCLEAR` / `UNKNOWN` / `LOW_CONFIDENCE` 인 경우.
- **Strategy**: 단정 답변 금지. "⚠️ 시행지침상 명확한 조항을 찾지 못했습니다.
  담당 기관 확인 권장" 형식으로 불확실성을 사용자에게 그대로 전달하고, 검색된
  상위 근거를 "참고" 수준으로만 표기. 직불금처럼 박탈 위험이 큰 도메인일수록
  엄격히 적용.
- **Pitfall**: LLM 이 도구의 UNCLEAR 신호를 무시하고 의무 가설 쪽 인용만 골라
  "필수입니다" 라고 단정하면, 사용자가 잘못된 행정 행동을 취해 직불금 박탈/감액
  위험. Calibration over confident-sounding answer.

## R9. 안전 민감 키워드 fast-path 거부
- **When**: 입력에 "농약", "직불", "진단" 등 안전 민감 키워드 포함.
- **Strategy**: fast-path 우회. 정상 LLM 경로 (서브에이전트 위임 + 인용 검증)
  강제.
- **Pitfall**: regex fast-path 가 잘못된 농약 권고를 즉답하면 안전 게이트
  무력화.

---

## 빈 슬롯 — 새 전략 추가 시 (append-only)

새 패턴 발견 시 `### Rn. 제목` 형식으로 위 섹션 아래에 append.
출처 한 줄 (날짜·평가 ID·트레이스 URL 또는 사람-검토 메모) 필수.
