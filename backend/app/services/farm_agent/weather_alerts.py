"""Deterministic crop-aware weather risk advisory.

Pure-Python analyzer over `get_weather()` payload (current + daily_forecasts).
No DB, no network, no LLM — just thresholds tuned for Korean field crops so
the agent (and the briefing prompt) can quote a stable, auditable risk list
instead of asking a small model to compare numbers itself.

Output shape (list of advisories, each):
    {
      "level":   "critical" | "warning" | "info",
      "kind":    short slug ("frost", "heatwave", "heavy_rain", ...)
      "when":    "오늘" | "오늘밤" | "내일" | "YYYY-MM-DD" | "지금"
      "message": Korean one-liner the agent can paste verbatim
      "value":   raw measured value (float | int) for downstream filtering
      "crop_hint": optional crop-specific note when main_crop is provided
    }

Why this lives here (not weather_client.py):
    weather_client.py is the I/O boundary — it shouldn't know about crops or
    actionable thresholds. This module is a small policy layer the agent
    (and tests) can exercise without a KMA key.
"""

from __future__ import annotations

from typing import Any

# ── Thresholds (Korean field-crop conventions) ──────────────────────────────
# Sources: 농촌진흥청 영농안전 기준, KMA 폭염/한파/강풍 특보 기준 (대략).
# 보수적으로 잡았다 — 임계 미만에서도 농민 판단으로 대응 가능하지만,
# 본 모듈이 critical 을 띄웠다면 실제 피해 가능성이 높다는 신호.

_FROST_TEMP_C = 2.0          # 야간 최저 ≤ 2℃ → 서리/동해 위험
_HARD_FROST_C = -2.0         # ≤ -2℃ → 강한 동해
_HEATWAVE_C = 33.0           # 일 최고 ≥ 33℃ → 고온 스트레스
_EXTREME_HEAT_C = 35.0       # ≥ 35℃ → 폭염 특보 수준
# Tropical night (열대야) — heatwave 가 일 최고(tmax) 신호라면, 열대야는 일 최저
# (tmin) 기반 별개 카테고리. 야간 25℃ 이상이 지속되면 작물 호흡량 증가로
# 광합성 동화산물이 밤 사이 소모 → 벼 등숙률·식미 저하, 사과·배 야간저온 부족
# 으로 착색 부진, 토마토·고추 수정·결실 장애. 액션도 다름 (낮 차광 vs 야간
# 환기·미스트 가동·관수 사이클 조정). KMA 열대야 기준 25℃ 채택, 초열대야
# (30℃) 보다 보수적인 27℃ 를 critical 로 두어 농업 임계로 재해석.
_TROPICAL_NIGHT_WARNING_C = 25.0
_TROPICAL_NIGHT_CRITICAL_C = 27.0
_HEAVY_RAIN_MM = 30.0        # 일강수량 ≥ 30mm → 침수/도복
_TORRENTIAL_RAIN_MM = 80.0   # ≥ 80mm → 호우 특보
_HIGH_PRECIP_PROB = 70       # 강수확률 ≥ 70% (정량값 없을 때 fallback)
_STRONG_WIND_MS = 9.0        # 풍속 ≥ 9m/s → 강풍 (육상 기준)
_GALE_WIND_MS = 14.0         # ≥ 14m/s → 강풍특보 수준
_FUNGAL_HUMIDITY_PCT = 85    # 일 평균 습도 ≥ 85% AND 적정 온도대 → 곰팡이병 호조

# 곰팡이병이 잘 번지는 온도대 — 대부분 노균/잿빛곰팡이/탄저 공통.
_FUNGAL_TEMP_LO = 15.0
_FUNGAL_TEMP_HI = 25.0

# Drought (forecast-driven) — 연속 무강수일 임계.
# KMA 단기예보는 보통 3일, 중기예보까지 합쳐 7~10일 가능. 임계는 보수적.
_DRY_DAY_PRECIP_MM = 1.0       # 일강수 < 1mm 이면 "건조일" 로 카운트
_DROUGHT_WARNING_DAYS = 5      # 5일 연속 무강수 → 경고
_DROUGHT_CRITICAL_DAYS = 7     # 7일 연속 무강수 → 심각
_DROUGHT_SCAN_DAYS = 14        # 가뭄 스캔은 horizon_days 무시하고 별도 한도

# Snow / 폭설 — daily_forecasts.sky 가 눈/진눈깨비/눈날림 류일 때 precipitation
# (water-equivalent mm) 을 적설 추정 프록시로 사용. 1mm 강수 ≈ 1cm 신적설 근사
# (실제 비율은 5~15:1 범위로 변동하지만 KMA daily 집계에는 적설 cm 직접값이
# 없어 보수적으로 1:1 가정). 시설하우스 붕괴 임계는 농촌진흥청 자료상 보통
# 10cm 누적부터 위험으로 본다.
_SNOW_PRECIP_MM_WARNING = 5.0    # ≥ 5mm + 눈 sky → warning (≈ 5cm 이상)
_SNOW_PRECIP_MM_CRITICAL = 10.0  # ≥ 10mm + 눈 sky → critical (≈ 10cm 이상)
_SNOW_SKY_MARKERS: tuple[str, ...] = ("눈", "진눈깨비", "눈날림", "비/눈")

# Diurnal swing (일교차) — frost/heatwave 와 독립; 16℃부터 사과·토마토 열과,
# 배추 추대, 벼 등숙 부진의 직접 신호 (한국 평균 8~12℃, 봄·가을 14~16℃ 정상).
_DIURNAL_RANGE_WARNING_C = 16.0   # ≥16℃ 경고; ≥20℃ critical (열과·갈라짐 빈발)
_DIURNAL_RANGE_CRITICAL_C = 20.0

# Cold wave (한파/혹한) — frost (≤2℃) 와는 의미·대응 모두 다른 별개 카테고리.
# KMA 한파특보 기준은 약 -12℃ (24h 변화량/지속일 등 복합 지표) — 농업에선 절대
# tmin -10℃ 부터 시설 동파·월동작물 동해·외부 작업 안전이 본격 영향. frost 의
# "보온·살수" 액션은 -5℃ 이하에선 즉시 동결되어 역효과이므로, cold_wave 는
# "시설 난방 가동·동파 방지·월동 보온재" 라는 다른 액션 셋을 권한다. frost 와
# 동시 발화 허용 (서로 다른 의사결정 슬롯).
_COLD_WAVE_WARNING_C = -10.0      # tmin ≤ -10℃ → warning
_COLD_WAVE_CRITICAL_C = -15.0     # tmin ≤ -15℃ → critical (한파특보 강화 수준)

# Wind chill (체감온도) — frost/cold_wave 의 절대온도 임계와 별개로 야외 작업 안전을
# 결정하는 신호. 같은 -5℃ 라도 무풍 vs 풍속 10m/s 는 손가락·귀 동상 위험이 자릿수
# 차이. Environment Canada 공식 (KMA 도 동일 모델 사용):
#     WC = 13.12 + 0.6215·T - 11.37·V^0.16 + 0.3965·T·V^0.16
#   T: 기온(℃),  V: 풍속(km/h),  공식 유효구간: T ≤ 10℃, V ≥ 4.8km/h (≈1.34m/s).
# 임계는 동상 의학 가이드라인:
#   ≤ -10℃ : 장시간 노출 시 동상 위험 → warning
#   ≤ -20℃ : 노출 피부 30분 이내 동상 → critical
_WIND_CHILL_WARNING_C = -10.0
_WIND_CHILL_CRITICAL_C = -20.0
_WIND_CHILL_MAX_TEMP_C = 10.0     # T > 10℃ 면 공식 미적용
_WIND_CHILL_MIN_WIND_MS = 1.34    # V < 4.8 km/h 면 공식 미적용

# Pest pressure (병해충 호조 환경) — 곰팡이성 이외 해충 발생 호조 패턴.
# fungal_humidity 가 다습+적정온도(곰팡이성 병원균 호조) 시그널이라면, 다음 두
# kind 는 건조 + 온도 패턴으로 해충 폭발(개체수 1~2주 배가) 시점을 미리 잡는다.
# 출처: 농촌진흥청 작물보호 일반론(점박이응애·복숭아진딧물 등 발생 환경). 임계는
# 일별 평균 습도 + 일 최고기온 조합으로 단순화 (예보에 시간별이 없음).
# 보수적으로 잡았다 — 일상적 봄·가을 날씨로는 점화하지 않게 하고, 진짜 호조
# 패턴(폭염 직전 / 가뭄 동반)에서만 정보성 advisory(level=info) 를 띄운다.
_MITES_TEMP_HI_C = 28.0           # tmax ≥ 28℃ + 저습 → 점박이·차응애 호조
_MITES_HUMIDITY_HI_PCT = 60.0     # humidity_avg ≤ 60%
_APHID_TEMP_LO_C = 20.0           # 진딧물 호조 온도대 (봄·가을 패턴, 보수적 lo)
_APHID_TEMP_HI_C = 27.0
_APHID_HUMIDITY_HI_PCT = 50.0     # humidity_avg ≤ 50% — 다습은 진딧물 곰팡이병으로
                                  # 자가 억제됨, 50% 이하만 호조 (≥85% 는 fungal 영역).


# ── Crop-specific sensitivities ─────────────────────────────────────────────
# 키 = main_crop 의 prefix substring 매칭 (사용자가 "방울토마토" 라 적어도 매칭).
# 값 = {kind → 한국어 추가 한 줄}. 임계 자체는 공용이고, 작물별 코멘트만 다름.

_CROP_HINTS: dict[str, dict[str, str]] = {
    "사과": {
        "frost":      "개화기 서리 시 결실률 급락. 살수·송풍기 가동 검토.",
        "heatwave":   "고온은 일소피해(과실 화상) 위험 — 차광망/관수 보강.",
        "tropical_night": "야간 최저 25℃ 지속 시 착색·당도 부진 — 야간 관수·통기, 봉지·반사필름 검토.",
        "strong_wind": "낙과 위험 — 방풍망·지주 점검.",
        "heavy_rain": "잎/과실 곰팡이 (탄저·갈색무늬) 주의, 비 그친 직후 약제 살포.",
        "drought":    "비대기 수분 부족 시 과실 비대 정체·열과 — 관수 사이클 단축.",
        "snow":       "가지 부러짐 위험 — 적설 후 즉시 털어내기, 지주·유인끈 점검.",
        "temp_swing": "큰 일교차는 열과(과실 갈라짐) 직격 — 토양 수분 균일 유지·점적 사이클 안정화.",
        "cold_wave":  "휴면기 동해 한계 (-25℃대) 접근 시 주간지·원줄기 동해 위험 — 백색 도포·바람막이 점검.",
        "wind_chill": "전정·잔가지 정리는 풍속 약한 한낮으로 이동, 보온 장갑·다중 작업복으로 손가락 동상 예방.",
        "mites_window": "잎 뒷면 점박이응애 예찰 — 흰 점·황화 시 즉시 약제 살포 또는 천적(칠레이리응애) 방사 검토.",
        "aphid_window": "신초 사과혹진딧물 점검 — 잎말림 발견 시 초기 약제 1회로 차단.",
    },
    "배": {
        "frost":      "개화기 서리 직격 — 야간 살수 또는 송풍 권장.",
        "strong_wind": "낙과·가지 부러짐 위험.",
        "tropical_night": "야간고온은 과실 호흡 증가로 당도 정체·과피 색택 부진 — 야간 관수·통풍 강화.",
        "drought":    "비대기 가뭄 시 과실 발달 저해 — 점적관수 검토.",
        "snow":       "가지 부러짐 — 적설 직후 털어내고 골절지 처리.",
        "temp_swing": "비대 후기 일교차 급증은 과실 비대 불균일·열과 — 관수 안정화.",
        "cold_wave":  "꽃눈 동해 위험 (-20℃대 접근) — 동계 전정 보류·주간지 보호.",
        "wind_chill": "전정 작업 시간대를 풍속 약한 한낮으로 조정, 손가락 동상 위험 — 보온 장갑 필수.",
        "mites_window": "잎 뒷면 점박이응애 예찰 — 황화 잎 발견 시 즉시 약제 또는 천적 방사.",
        "aphid_window": "신초·꽃눈 진딧물 발생 가능 — 흑성병 매개 차원에서 조기 점검.",
    },
    "포도": {
        "frost":      "신초 동해 가능 — 비닐멀칭/터널 보온 검토.",
        "heatwave":   "송이 일소피해, 당도 저하 우려.",
        "tropical_night": "야간고온은 호흡으로 당분 소모 → 당도 정체·착색 부진. 야간 비가림 환기 풀가동.",
        "heavy_rain": "열과·노균병 위험 — 우산식 비가림 점검.",
        "drought":    "착색기 과습/건조 교차로 열과 — 토양수분 균일 유지.",
        "snow":       "비가림 시설 적설하중 — 측면 비닐 일시 개방·하중 분산.",
        "temp_swing": "착색기 큰 일교차는 당도엔 유리하나 열과 동반 — 점적 균일 가동.",
        "cold_wave":  "주간지 동해 위험 — 매몰·짚 피복·백색 도포 점검.",
        "wind_chill": "전정·매몰 작업은 풍속 약한 시간대로 이동, 손·발 보온 강화.",
        "mites_window": "비가림 내부 차응애 호조 — 잎 뒷면 점검·관수로 잎 표면 습도 보강.",
        "aphid_window": "신초 진딧물 — 황색 점착 트랩 설치, 발생 초기 약제 1회.",
    },
    "토마토": {
        "frost":      "10℃ 이하부터 생육정지 — 보온 필수.",
        "heatwave":   "수정 불량(공동과·기형과) — 차광·관수.",
        "tropical_night": "야간 25℃ 초과 지속 시 화분 활력 저하·착과 불량 — 시설 야간 환기팬·외기 도입·미스트로 25℃ 이하 유지.",
        "fungal_humidity": "잎곰팡이병/노균병 호조 — 환기 강화.",
        "drought":    "비대기 수분 부족 시 배꼽썩음과 급증 — 칼슘+관수 보강.",
        "snow":       "비닐하우스 적설하중 — 측창 보온재 정리, 야간 난방 가동·환기로 적설 미끄러뜨리기.",
        "temp_swing": "큰 일교차는 갈라짐·기형과·배꼽썩음 빈발 — 야간 보온·점적 안정화.",
        "cold_wave":  "시설 난방기 풀가동·내장 보온커튼 이중화. 관수 라인 동파 — 야간 미세 흐름 유지.",
        "mites_window": "시설 내 점박이응애 폭발 위험 — 잎 뒷면 일제 점검, 칠레이리응애 방사 검토.",
        "aphid_window": "온실가루이·진딧물 동시 호조 — 천적(콜레마니진디벌) 또는 황색 점착 트랩.",
    },
    "오이": {
        "frost":      "냉해에 매우 취약 — 야간 보온 필수.",
        "fungal_humidity": "노균병 폭발 위험 — 즉시 환기.",
        "tropical_night": "야간 호흡 증가로 곡과·낙화·품질 저하 — 시설 천창·측창 야간 개방, 미스트로 온도·습도 보정.",
        "drought":    "수분 결핍 시 곡과·낙과 — 관수 빈도 상향.",
        "snow":       "비닐하우스 적설하중 — 난방 가동·물뿌리기로 지붕 적설 차단.",
        "temp_swing": "큰 일교차는 곡과·낙화 — 시설 야간 보온 강화.",
        "cold_wave":  "한계온도 8℃ — 시설 난방 풀가동·이중커튼·근권부 보온재 추가.",
        "mites_window": "차응애·점박이응애 호조 — 잎 뒷면 점검, 시설은 미스트 분무로 습도 보강.",
        "aphid_window": "목화진딧물 발생 호조 — CMV 바이러스 매개 차원에서 조기 차단 필수.",
    },
    "딸기": {
        "frost":      "관부 동해 위험 — 부직포·수막재배 점검.",
        "heatwave":   "꽃눈 분화 저해 — 차광·환기.",
        "tropical_night": "야간 25℃ 이상은 꽃눈 분화·휴면 타파에 결정적 악영향 — 야간 환기·차광·근권부 냉수 관수 검토.",
        "fungal_humidity": "잿빛곰팡이병 위험 — 환기·약제 사이클 단축.",
        "snow":       "하우스 측면 적설 정리, 수막재배 동결 주의 — 야간 가동 점검.",
        "temp_swing": "큰 일교차는 기형과·열과 — 야간 보온·환기 균형.",
        "cold_wave":  "수막재배 펌프 동결·관 막힘 점검. 비수막은 부직포 다중 피복.",
        "mites_window": "점박이응애 호조 — 잎 뒷면 흰 점·황화 즉시 점검, 천적 방사 우선 검토.",
        "aphid_window": "딸기뿌리진딧물·복숭아진딧물 발생 — 새순 점검, 약제는 수정벌 영향 고려.",
    },
    "고추": {
        "frost":      "정식 직후 냉해 치명적 — 보온 터널.",
        "strong_wind": "쓰러짐·가지 부러짐 — 지주 보강.",
        "tropical_night": "야간고온은 화분 활력 저하·낙화·낙과 가속 — 시설은 야간 환기·외기 도입, 노지는 관수로 근권부 온도 완화.",
        "heavy_rain": "역병/탄저 폭발 위험 — 배수로 점검.",
        "drought":    "착과기 가뭄 시 낙화·낙과 — 점적관수 가동.",
        "snow":       "노지 월동 시 멀칭·부직포 보완. 시설재배는 적설하중 점검.",
        "temp_swing": "착과기 큰 일교차는 낙화·낙과 — 야간 보온, 시설은 변온관리.",
        "cold_wave":  "노지 잔존 포기는 즉시 정리. 시설재배는 난방·이중커튼 풀가동.",
        "wind_chill": "노지 정리·시설 외부 점검 작업은 한낮 풍속 약한 시간대, 보온복·체온 유지.",
        "mites_window": "차응애·점박이응애 호조 — 잎 뒷면 점검, 차광·미스트로 미기후 보정.",
        "aphid_window": "총채벌레·진딧물 동시 발생 가능 — 황색·청색 점착 트랩 병행, CMV 매개 차단.",
    },
    "벼": {
        "strong_wind": "출수기 도복 위험 — 물 빼기·낙수 검토.",
        "cold_stress": "야간 17℃ 이하 지속 시 냉해 가능 (출수~등숙기).",
        "heavy_rain": "도복·관수 피해 — 즉시 배수.",
        "drought":    "이앙·활착기 무강수는 분얼 저해 — 담수 깊이 점검.",
        "temp_swing": "등숙기 큰 일교차는 야간 저온으로 등숙률 저하 — 담수 깊이로 야간 보온.",
        "tropical_night": "출수·등숙기 야간 25℃ 초과는 호흡 증가로 등숙률·식미 저하·단백질 함량 상승 — 담수 깊이 깊게 유지하여 야간 수온 완충.",
    },
    "배추": {
        "frost":      "결구기 동해 — 부직포 점검.",
        "strong_wind": "잎 찢김·뿌리 흔들림.",
        "drought":    "결구기 가뭄 시 결구 부진·열구 — 관수 보강.",
        "snow":       "결구기 적설은 결구부 동상해 — 수확 임박 포기는 즉시 수확 검토.",
        "temp_swing": "정식 직후 큰 일교차는 추대(꽃대) 가속 위험 — 보온·정식 시기 검토.",
        "cold_wave":  "잔존 포기 즉시 수확. 저장 배추는 저장고 온도(0~3℃) 안정 확인.",
        "wind_chill": "잔존 포기 수확·정리 작업은 풍속 약한 시간대 우선, 손·발 동상 예방.",
        "aphid_window": "복숭아진딧물·배추진딧물 발생 호조 — TuMV 모자이크병 매개, 결구 전 조기 차단.",
    },
    "마늘": {
        "frost":      "월동 직후 발아 시 동해 — 멀칭 점검.",
        "drought":    "구비대기 수분 부족 시 인편 발달 저해.",
        "snow":       "월동기 적설은 보온 효과도 있으나, 융설 시 침수·습해 주의 — 배수로 점검.",
        "cold_wave":  "월동기 한계온도 (-8℃) 초과 — 부직포·왕겨 보온 강화, 배수로 동결 점검.",
        "wind_chill": "월동 점검·배수로 정비는 한낮 시간대로, 보온·동상 예방.",
    },
    "양파": {
        "heavy_rain": "수확기 비는 부패·저장성 저하.",
        "drought":    "비대기(4~5월) 가뭄은 구 크기 직격 — 점적관수.",
        "snow":       "월동기 멀칭·부직포 점검. 융설 후 습해로 인한 무름병 주의.",
        "cold_wave":  "월동기 동해 — 부직포 다중 피복, 노출 묘는 멀칭 추가.",
        "wind_chill": "월동 점검·멀칭 보강 작업은 풍속 약한 시간대, 손가락 동상 주의.",
    },
}


def _match_crop_hint(main_crop: str | None, kind: str) -> str | None:
    """Return crop-specific addendum for (crop, kind), or None."""
    if not main_crop:
        return None
    name = main_crop.strip()
    for key, hints in _CROP_HINTS.items():
        if key in name:
            return hints.get(kind)
    return None


def _label_for_offset(offset: int) -> str:
    if offset == 0:
        return "오늘"
    if offset == 1:
        return "내일"
    if offset == 2:
        return "모레"
    return f"D+{offset}"


def _safe_num(value: Any) -> float | None:
    """Coerce KMA/mock fields to float; None on missing or unparseable."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    """Same shape as _safe_num but for ints. KMA may emit numeric fields as
    strings ("0", "1"); booleans coerce safely; everything else returns None."""
    if value is None or isinstance(value, bool):
        # bool is an int subclass — explicitly excluded so True doesn't sneak
        # through as 1.
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _detect_drought(
    daily: list[Any],
    current: dict[str, Any],
    main_crop: str | None,
) -> dict[str, Any] | None:
    """Count leading consecutive 무강수 days; return advisory or None.

    규칙:
      - 현재 강수 중이면 (precipitation_type ∈ {비/눈/...} AND precip ≥ 1mm)
        가뭄 신호 무력화 — None.
      - daily_forecasts 첫 항목부터 순서대로 강수<1mm 인 날을 카운트.
      - 강수>=1mm 또는 precip 누락(_safe_num=None)을 만나면 즉시 중단
        (예보 결측을 "건조"로 오인하지 않게).
      - streak < 5 일이면 advisory 없음.
      - 5~6일 → warning, 7일 이상 → critical.
    """
    cur_pty = (current.get("precipitation_type") or "").strip()
    cur_precip = _safe_num(current.get("precipitation")) or 0.0
    if cur_pty and cur_pty not in {"없음", "0"} and cur_precip >= _DRY_DAY_PRECIP_MM:
        return None

    streak = 0
    for entry in daily[:_DROUGHT_SCAN_DAYS]:
        if not isinstance(entry, dict):
            break
        precip = _safe_num(entry.get("precipitation"))
        if precip is None:
            break  # 결측은 가뭄 카운트 중단 (보수적)
        if precip < _DRY_DAY_PRECIP_MM:
            streak += 1
        else:
            break

    if streak < _DROUGHT_WARNING_DAYS:
        return None

    level = "critical" if streak >= _DROUGHT_CRITICAL_DAYS else "warning"
    return {
        "level": level,
        "kind": "drought",
        "when": f"향후 {streak}일",
        "message": (
            f"향후 {streak}일 연속 무강수(<1mm) 예보 — "
            "토양 건조·관수 계획 점검."
        ),
        "value": streak,
        "crop_hint": _match_crop_hint(main_crop, "drought"),
    }


def _compute_wind_chill(t_c: float, v_ms: float) -> float | None:
    """Return Environment Canada wind chill in °C, or None if formula invalid.

    공식은 T ≤ 10℃ AND V ≥ 4.8km/h 에서만 검증됨. 검증범위 밖에서는 None
    반환 (호출자가 advisory 발생을 건너뛰게 한다 — 외삽 금지).
    """
    if t_c > _WIND_CHILL_MAX_TEMP_C or v_ms < _WIND_CHILL_MIN_WIND_MS:
        return None
    v_kmh = v_ms * 3.6
    v_pow = v_kmh ** 0.16
    return 13.12 + 0.6215 * t_c - 11.37 * v_pow + 0.3965 * t_c * v_pow


def _is_snow_sky(sky: Any) -> bool:
    """Whether a daily forecast `sky` label indicates snow precipitation.

    KMA PTY 코드 2(비/눈)/3(눈)/6(진눈깨비)/7(눈날림) 가 _PTY_MAP 으로 한국어 라벨이
    되어 daily.sky 에 들어온다. 비-snow 키워드는 절대 매칭되지 않도록 정확
    토큰만 검사 (substring "눈" 만 보면 "눈비"는 잡지만 "눈높이" 같은 비-기상
    문자열은 daily.sky 에 들어올 수 없으니 안전).
    """
    if not isinstance(sky, str):
        return False
    return any(marker in sky for marker in _SNOW_SKY_MARKERS)


def analyze_weather_risks(
    weather: dict[str, Any] | None,
    main_crop: str | None = None,
    *,
    horizon_days: int = 5,
) -> list[dict[str, Any]]:
    """Build a list of crop-aware risk advisories from a get_weather() payload.

    Args:
        weather: dict shaped like get_weather() output. Tolerant of missing
            keys — empty input → empty list (callers safely chain).
        main_crop: free-form Korean crop name. Used only for crop_hint
            decoration; thresholds themselves are crop-agnostic.
        horizon_days: how many `daily_forecasts` entries to scan (default 5,
            matching KMA 단기+중기 결합 daily_forecasts 길이).

    Returns:
        List of advisory dicts ordered by (severity desc, day asc). Empty when
        no thresholds tripped — caller can render "특이사항 없음".
    """
    if not isinstance(weather, dict):
        return []

    advisories: list[dict[str, Any]] = []

    current = weather.get("current") or {}
    cur_wind = _safe_num(current.get("wind_speed"))
    cur_temp = _safe_num(current.get("temperature"))
    cur_pty = (current.get("precipitation_type") or "").strip()
    cur_precip = _safe_num(current.get("precipitation")) or 0.0

    # 지금 강수가 강한 경우 — daily 평균보다 즉각적인 신호.
    if cur_pty and cur_pty not in {"없음", "0"} and cur_precip >= 5.0:
        advisories.append(
            {
                "level": "warning",
                "kind": "current_rain",
                "when": "지금",
                "message": f"현재 {cur_pty} {cur_precip:.0f}mm/h — 야외 작업·방제 보류 권장.",
                "value": cur_precip,
                "crop_hint": _match_crop_hint(main_crop, "heavy_rain"),
            }
        )

    if cur_wind is not None and cur_wind >= _STRONG_WIND_MS:
        level = "critical" if cur_wind >= _GALE_WIND_MS else "warning"
        advisories.append(
            {
                "level": level,
                "kind": "strong_wind",
                "when": "지금",
                "message": f"현재 풍속 {cur_wind:.1f}m/s — 약제 살포·드론 작업 금지.",
                "value": cur_wind,
                "crop_hint": _match_crop_hint(main_crop, "strong_wind"),
            }
        )

    daily = weather.get("daily_forecasts") or []
    if not isinstance(daily, list):
        daily = []

    for entry in daily[:horizon_days]:
        if not isinstance(entry, dict):
            continue
        offset = _coerce_int(entry.get("day_offset"))
        when = (
            _label_for_offset(offset)
            if offset is not None
            else (entry.get("date") or "예보")
        )
        tmin = _safe_num(entry.get("temp_min"))
        tmax = _safe_num(entry.get("temp_max"))
        wmax = _safe_num(entry.get("wind_speed_max"))
        precip = _safe_num(entry.get("precipitation")) or 0.0
        pprob = _safe_num(entry.get("precipitation_prob"))
        humidity = _safe_num(entry.get("humidity_avg"))

        # Frost / hard frost
        if tmin is not None and tmin <= _FROST_TEMP_C:
            level = "critical" if tmin <= _HARD_FROST_C else "warning"
            advisories.append(
                {
                    "level": level,
                    "kind": "frost",
                    "when": when,
                    "message": f"{when} 최저 {tmin:.1f}℃ — 서리/동해 가능, 보온·살수 검토.",
                    "value": tmin,
                    "crop_hint": _match_crop_hint(main_crop, "frost"),
                }
            )

        # Cold wave (한파/혹한) — frost 와 별개 임계·별개 액션 셋.
        # tmin ≤ -10℃ 부터 살수는 즉시 동결로 역효과 → "시설 난방·동파 방지·월동
        # 보온재" 메시지로 분리. frost 와 동시 발화 가능 (의사결정 분리 의도).
        if tmin is not None and tmin <= _COLD_WAVE_WARNING_C:
            level = "critical" if tmin <= _COLD_WAVE_CRITICAL_C else "warning"
            advisories.append(
                {
                    "level": level,
                    "kind": "cold_wave",
                    "when": when,
                    "message": (
                        f"{when} 최저 {tmin:.1f}℃ — 한파, 시설 난방 가동·"
                        "외부 수도/관수 라인 동파 방지, 야외 작업 시간 단축."
                    ),
                    "value": tmin,
                    "crop_hint": _match_crop_hint(main_crop, "cold_wave"),
                }
            )

        # Wind chill (체감온도) — frost/cold_wave 와 독립. tmin + wmax 조합으로
        # 보수적 worst-case (이른 새벽 + 강풍) 추정. 공식 유효구간 밖이면 None →
        # advisory 발생 안 함 (외삽 금지).
        if tmin is not None and wmax is not None:
            wc = _compute_wind_chill(tmin, wmax)
            if wc is not None and wc <= _WIND_CHILL_WARNING_C:
                level = "critical" if wc <= _WIND_CHILL_CRITICAL_C else "warning"
                advisories.append(
                    {
                        "level": level,
                        "kind": "wind_chill",
                        "when": when,
                        "message": (
                            f"{when} 체감 약 {wc:.0f}℃ "
                            f"(기온 {tmin:.0f}℃·풍속 {wmax:.0f}m/s) — "
                            "야외 작업 시간 단축·보온복 착용·동상 예방."
                        ),
                        "value": round(wc, 1),
                        "crop_hint": _match_crop_hint(main_crop, "wind_chill"),
                    }
                )

        # Heatwave
        if tmax is not None and tmax >= _HEATWAVE_C:
            level = "critical" if tmax >= _EXTREME_HEAT_C else "warning"
            advisories.append(
                {
                    "level": level,
                    "kind": "heatwave",
                    "when": when,
                    "message": f"{when} 최고 {tmax:.1f}℃ — 고온 스트레스, 관수·차광 보강.",
                    "value": tmax,
                    "crop_hint": _match_crop_hint(main_crop, "heatwave"),
                }
            )

        # Tropical night (열대야) — heatwave 와 별개 임계·별개 액션 셋.
        # tmin ≥ 25℃ 부터 작물 야간 호흡 증가로 광합성 동화산물 손실 → 벼
        # 등숙률·식미 저하, 사과·배 착색 부진, 토마토·고추 수정 불량. 낮 차광
        # 만으로는 해결 안 됨 — "야간 환기·미스트·관수 사이클" 권고. heatwave
        # 와 동시 발화 가능 (의사결정 분리 의도).
        if tmin is not None and tmin >= _TROPICAL_NIGHT_WARNING_C:
            level = (
                "critical" if tmin >= _TROPICAL_NIGHT_CRITICAL_C else "warning"
            )
            advisories.append(
                {
                    "level": level,
                    "kind": "tropical_night",
                    "when": when,
                    "message": (
                        f"{when} 야간 최저 {tmin:.1f}℃ — 열대야, 야간 환기·"
                        "미스트 가동·관수 사이클 조정으로 작물 호흡 부담 완화."
                    ),
                    "value": tmin,
                    "crop_hint": _match_crop_hint(main_crop, "tropical_night"),
                }
            )

        # Heavy rain — prefer measured precip; else fall back to high prob.
        if precip >= _HEAVY_RAIN_MM:
            level = "critical" if precip >= _TORRENTIAL_RAIN_MM else "warning"
            advisories.append(
                {
                    "level": level,
                    "kind": "heavy_rain",
                    "when": when,
                    "message": f"{when} 강수 {precip:.0f}mm 예상 — 배수로 점검·시설 점검.",
                    "value": precip,
                    "crop_hint": _match_crop_hint(main_crop, "heavy_rain"),
                }
            )
        elif pprob is not None and pprob >= _HIGH_PRECIP_PROB and precip < _HEAVY_RAIN_MM:
            advisories.append(
                {
                    "level": "info",
                    "kind": "rain_likely",
                    "when": when,
                    "message": f"{when} 강수확률 {int(pprob)}% — 야외 작업 일정 조정 검토.",
                    "value": pprob,
                    "crop_hint": _match_crop_hint(main_crop, "heavy_rain"),
                }
            )

        # Strong wind (forecast)
        if wmax is not None and wmax >= _STRONG_WIND_MS:
            level = "critical" if wmax >= _GALE_WIND_MS else "warning"
            advisories.append(
                {
                    "level": level,
                    "kind": "strong_wind",
                    "when": when,
                    "message": f"{when} 풍속 {wmax:.1f}m/s 예상 — 방제·드론 작업 보류.",
                    "value": wmax,
                    "crop_hint": _match_crop_hint(main_crop, "strong_wind"),
                }
            )

        # Snow / 폭설 — daily.sky 가 눈/진눈깨비/눈날림 류 + 강수량(water-equivalent)
        # 임계 충족 시. 적설 cm 직접값 부재로 1mm ≈ 1cm 보수적 가정. 시설하우스
        # 적설하중 위험 (≈10cm+) 을 critical 로 띄워 호출자가 즉시 조치를 권장
        # 할 수 있게 한다. heavy_rain 과 동시 발화 가능 — 둘 다 유의미한 신호.
        if precip >= _SNOW_PRECIP_MM_WARNING and _is_snow_sky(entry.get("sky")):
            level = (
                "critical" if precip >= _SNOW_PRECIP_MM_CRITICAL else "warning"
            )
            advisories.append(
                {
                    "level": level,
                    "kind": "snow",
                    "when": when,
                    "message": (
                        f"{when} 적설 약 {precip:.0f}cm 예상 — "
                        "시설 적설하중·교통·낙상 위험."
                    ),
                    "value": precip,
                    "crop_hint": _match_crop_hint(main_crop, "snow"),
                }
            )

        # Diurnal swing (일교차) — frost/heatwave 와 독립적 신호.
        if tmin is not None and tmax is not None and (swing := tmax - tmin) >= _DIURNAL_RANGE_WARNING_C:
            level = "critical" if swing >= _DIURNAL_RANGE_CRITICAL_C else "warning"
            advisories.append({
                "level": level, "kind": "temp_swing", "when": when,
                "value": swing,
                "message": (
                    f"{when} 일교차 {swing:.0f}℃ ({tmin:.0f}~{tmax:.0f}℃) — "
                    "열과·갈라짐·추대 가속 위험."
                ),
                "crop_hint": _match_crop_hint(main_crop, "temp_swing"),
            })

        # Fungal-friendly humidity window
        if (
            humidity is not None
            and humidity >= _FUNGAL_HUMIDITY_PCT
            and tmax is not None
            and _FUNGAL_TEMP_LO <= tmax <= _FUNGAL_TEMP_HI
        ):
            advisories.append(
                {
                    "level": "info",
                    "kind": "fungal_humidity",
                    "when": when,
                    "message": (
                        f"{when} 습도 {int(humidity)}% / 기온 {tmax:.0f}℃ — "
                        "곰팡이성 병원균 호조 환경, 환기·예방 약제 검토."
                    ),
                    "value": humidity,
                    "crop_hint": _match_crop_hint(main_crop, "fungal_humidity"),
                }
            )

        # Mites window (점박이·차응애) — 고온 + 저습. fungal_humidity 와 정반대
        # 시그널이라 동시 발화 불가능. 응애는 잎 뒷면 흡즙으로 잎 색·광합성 직격.
        if (
            humidity is not None
            and humidity <= _MITES_HUMIDITY_HI_PCT
            and tmax is not None
            and tmax >= _MITES_TEMP_HI_C
        ):
            advisories.append(
                {
                    "level": "info",
                    "kind": "mites_window",
                    "when": when,
                    "message": (
                        f"{when} 기온 {tmax:.0f}℃ / 습도 {int(humidity)}% — "
                        "응애(점박이·차응애) 발생 호조, 잎 뒷면 점검·예찰 권장."
                    ),
                    "value": humidity,
                    "crop_hint": _match_crop_hint(main_crop, "mites_window"),
                }
            )

        # Aphid window (진딧물) — 따뜻 + 건조. 봄·가을 호조 패턴. 진딧물은 다습·
        # 고온에선 자가 억제(곤충병원성 곰팡이 발병)되므로 좁은 온도대로 한정.
        if (
            humidity is not None
            and humidity <= _APHID_HUMIDITY_HI_PCT
            and tmax is not None
            and _APHID_TEMP_LO_C <= tmax <= _APHID_TEMP_HI_C
        ):
            advisories.append(
                {
                    "level": "info",
                    "kind": "aphid_window",
                    "when": when,
                    "message": (
                        f"{when} 기온 {tmax:.0f}℃ / 습도 {int(humidity)}% — "
                        "진딧물 발생 호조, 새순 점검·황색 점착 트랩 설치 검토."
                    ),
                    "value": humidity,
                    "crop_hint": _match_crop_hint(main_crop, "aphid_window"),
                }
            )

    # Drought — separate scan over the full daily list (ignores horizon_days).
    # 의도적으로 horizon_days 와 분리: frost/heatwave 는 단일 일자 신호고
    # 가뭄은 다중 일자에 걸친 누적 신호이므로 같은 윈도우에 묶지 않는다.
    drought = _detect_drought(daily, current, main_crop)
    if drought is not None:
        advisories.append(drought)

    # Sort: critical → warning → info, then by when (지금 < 오늘 < 내일 < ...)
    # Drought ("향후 Nd") 는 다중 일자 신호이므로 같은 severity 안에서 단일-일자
    # advisory 뒤에 오도록 큰 rank(60) 를 부여.
    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    when_rank = {"지금": 0, "오늘": 1, "오늘밤": 2, "내일": 3, "모레": 4}

    def _when_key(when_str: str) -> int:
        if when_str.startswith("향후"):
            return 60
        return when_rank.get(when_str, 50)

    advisories.sort(
        key=lambda a: (
            severity_rank.get(a.get("level", "info"), 99),
            _when_key(a.get("when", "")),
            a.get("when", ""),
        )
    )
    return advisories


# Per-day "task decision" status: blocked (작업 보류) > caution (주의) > ok (진행).
# blocked 는 critical advisory 또는 강수/적설(작업 자체가 의미 없거나 위험) 시,
# caution 은 warning level 또는 풍속·일교차 같은 "주의해서 진행" 신호일 때.
_STATUS_TITLE: dict[str, str] = {
    "blocked": "작업 보류 권장",
    "caution": "주의하여 진행",
    "ok": "예정 작업 진행 가능",
}
_STATUS_TYPE: dict[str, str] = {
    "blocked": "방제",
    "caution": "주의",
    "ok": "작업",
}
# kind → 우선 액션 한 줄. 작물·심각도와 무관한 일반 액션 컨텍스트.
_KIND_ACTION: dict[str, str] = {
    "frost":         "야간 보온재·살수 점검, 개화기 작물 우선 보호.",
    "cold_wave":     "시설 난방 가동·관수 라인 동파 방지·외부 작업 단축.",
    "wind_chill":    "야외 작업은 풍속 약한 한낮으로 이동, 보온복·동상 예방.",
    "heatwave":      "관수 사이클 단축·차광망 가동·정오 전후 작업 회피.",
    "tropical_night":"시설 야간 환기·미스트·관수 사이클 조정.",
    "heavy_rain":    "배수로 점검·시설 점검·약제 살포 보류.",
    "rain_likely":   "야외 작업·약제 살포 일정 재조정 검토.",
    "current_rain":  "야외 작업·방제 즉시 중단.",
    "snow":          "시설 적설하중 분산, 비닐하우스 측창 점검·교통 안전 확인.",
    "strong_wind":   "약제 살포·드론 작업 보류, 지주·방풍 시설 점검.",
    "temp_swing":    "관수 안정화·시설 야간 보온으로 열과·갈라짐 예방.",
    "fungal_humidity":"환기 강화·예방 약제 사이클 검토.",
    "mites_window":  "잎 뒷면 점검·예찰, 발생 초기 약제 또는 천적 방사 검토.",
    "aphid_window":  "신초·새순 점검, 황색 점착 트랩 설치·초기 약제 1회.",
    "drought":       "관수 사이클 단축·토양 수분 점검, 관정·저수지 가용량 확인.",
}

# blocked 처리 대상 — critical 이 아니어도 "작업 자체 불가/무의미" 카테고리.
_BLOCKING_KINDS: frozenset[str] = frozenset({
    "heavy_rain", "current_rain", "snow",
})


def build_task_advisories(
    weather: dict[str, Any] | None,
    main_crop: str | None = None,
    *,
    horizon_days: int = 5,
) -> list[dict[str, Any]]:
    """Per-day task decisions derived from `analyze_weather_risks`.

    Replaces the frontend's hardcoded if/precip>0/wind>=7 logic with a single
    agentic policy: same thresholds the LLM and briefing use, same crop-aware
    hints, same severity sort. Returned shape is intentionally stable so the
    UI can render directly without re-deriving status.

    Returns one entry per day in `weather.daily_forecasts[:horizon_days]`:
        {
          "date":      "YYYY-MM-DD",
          "when":      "오늘" | "내일" | "모레" | "D+N",
          "status":    "blocked" | "caution" | "ok",
          "title":     사용자에게 보일 한국어 제목,
          "type":      "방제" | "주의" | "작업",
          "summary":   왜 이 status 인지 한 줄 (advisory.message 또는 기본 문구),
          "actions":   ["관수 사이클 단축·...", ...]    # 우선 액션 1~3개
          "crop_hint": 작물별 부가 한 줄 (없으면 None),
          "advisories": [원본 advisory dict, ...]      # 디버그·툴팁용 풀 리스트
        }
    Empty `weather` 또는 `daily_forecasts` 누락 시 빈 리스트.
    """
    if not isinstance(weather, dict):
        return []
    daily = weather.get("daily_forecasts") or []
    if not isinstance(daily, list) or not daily:
        return []

    advisories = analyze_weather_risks(
        weather, main_crop=main_crop, horizon_days=horizon_days
    )

    # advisory.when ("오늘", "내일", date string) → daily index 매칭에 사용.
    # current_rain/지금-카테고리는 offset=0 (오늘) 카드에 합류시킨다.
    by_day: dict[str, list[dict[str, Any]]] = {}
    for a in advisories:
        when = a.get("when") or ""
        if when in {"지금", "오늘밤"}:
            when = "오늘"
        if when.startswith("향후"):
            # drought 는 단일 일자가 아니므로 0번째 (오늘) 카드에 attach.
            when = "오늘"
        by_day.setdefault(when, []).append(a)

    out: list[dict[str, Any]] = []
    for entry in daily[:horizon_days]:
        if not isinstance(entry, dict):
            continue
        date = entry.get("date")
        offset = _coerce_int(entry.get("day_offset"))
        when = _label_for_offset(offset) if offset is not None else (date or "예보")
        # 매칭: 오늘 카드는 _label_for_offset(0) == "오늘" 이지만, 실제 daily entry
        # 의 date 키 기반 advisory 도 흡수해야 한다 (offset 미설정 케이스).
        day_adv = list(by_day.get(when, []))
        if date and date != when:
            day_adv.extend(by_day.get(date, []))

        if any(a.get("level") == "critical" for a in day_adv):
            status = "blocked"
        elif any(a.get("kind") in _BLOCKING_KINDS for a in day_adv):
            status = "blocked"
        elif any(a.get("level") == "warning" for a in day_adv):
            status = "caution"
        else:
            status = "ok"

        # 가장 심각한 advisory (sort 가 이미 critical→warning→info) 하나를 summary 로.
        top = day_adv[0] if day_adv else None
        if top is not None:
            summary = top.get("message") or _STATUS_TITLE[status]
        else:
            summary = "5일 예보 기준으로 큰 기상 제한은 보이지 않습니다."

        # actions: 모든 advisory 의 kind 별 액션 (중복 제거, 최대 3개).
        seen: set[str] = set()
        actions: list[str] = []
        for a in day_adv:
            kind = a.get("kind") or ""
            action = _KIND_ACTION.get(kind)
            if action and action not in seen:
                actions.append(action)
                seen.add(action)
            if len(actions) >= 3:
                break

        # crop_hint: 첫 번째 비어있지 않은 작물별 힌트.
        crop_hint = next(
            (a.get("crop_hint") for a in day_adv if a.get("crop_hint")),
            None,
        )

        out.append({
            "date": date,
            "when": when,
            "status": status,
            "title": _STATUS_TITLE[status],
            "type": _STATUS_TYPE[status],
            "summary": summary,
            "actions": actions,
            "crop_hint": crop_hint,
            "advisories": day_adv,
        })
    return out


def format_advisories_markdown(
    advisories: list[dict[str, Any]],
    main_crop: str | None = None,
) -> str:
    """Render advisories as a compact Korean markdown block.

    Empty input → "## ⚠️ 기상 위험 요약\\n\\n특이사항 없음." so the agent can
    insert this verbatim into a briefing/answer without conditional logic.
    """
    if not advisories:
        return "## ⚠️ 기상 위험 요약\n\n특이사항 없음 (관측 임계 미달)."
    crop_label = f" · {main_crop}" if main_crop else ""
    lines = [f"## ⚠️ 기상 위험 요약{crop_label}"]
    icon = {"critical": "🔴", "warning": "🟠", "info": "🟡"}
    for a in advisories:
        bullet = icon.get(a.get("level", "info"), "•")
        lines.append(f"- {bullet} **[{a.get('when', '')}]** {a.get('message', '')}")
        hint = a.get("crop_hint")
        if hint:
            lines.append(f"  - {hint}")
    return "\n".join(lines)
