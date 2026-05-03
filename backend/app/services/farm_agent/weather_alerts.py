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


# ── Crop-specific sensitivities ─────────────────────────────────────────────
# 키 = main_crop 의 prefix substring 매칭 (사용자가 "방울토마토" 라 적어도 매칭).
# 값 = {kind → 한국어 추가 한 줄}. 임계 자체는 공용이고, 작물별 코멘트만 다름.

_CROP_HINTS: dict[str, dict[str, str]] = {
    "사과": {
        "frost":      "개화기 서리 시 결실률 급락. 살수·송풍기 가동 검토.",
        "heatwave":   "고온은 일소피해(과실 화상) 위험 — 차광망/관수 보강.",
        "strong_wind": "낙과 위험 — 방풍망·지주 점검.",
        "heavy_rain": "잎/과실 곰팡이 (탄저·갈색무늬) 주의, 비 그친 직후 약제 살포.",
        "drought":    "비대기 수분 부족 시 과실 비대 정체·열과 — 관수 사이클 단축.",
        "snow":       "가지 부러짐 위험 — 적설 후 즉시 털어내기, 지주·유인끈 점검.",
        "temp_swing": "큰 일교차는 열과(과실 갈라짐) 직격 — 토양 수분 균일 유지·점적 사이클 안정화.",
    },
    "배": {
        "frost":      "개화기 서리 직격 — 야간 살수 또는 송풍 권장.",
        "strong_wind": "낙과·가지 부러짐 위험.",
        "drought":    "비대기 가뭄 시 과실 발달 저해 — 점적관수 검토.",
        "snow":       "가지 부러짐 — 적설 직후 털어내고 골절지 처리.",
        "temp_swing": "비대 후기 일교차 급증은 과실 비대 불균일·열과 — 관수 안정화.",
    },
    "포도": {
        "frost":      "신초 동해 가능 — 비닐멀칭/터널 보온 검토.",
        "heatwave":   "송이 일소피해, 당도 저하 우려.",
        "heavy_rain": "열과·노균병 위험 — 우산식 비가림 점검.",
        "drought":    "착색기 과습/건조 교차로 열과 — 토양수분 균일 유지.",
        "snow":       "비가림 시설 적설하중 — 측면 비닐 일시 개방·하중 분산.",
        "temp_swing": "착색기 큰 일교차는 당도엔 유리하나 열과 동반 — 점적 균일 가동.",
    },
    "토마토": {
        "frost":      "10℃ 이하부터 생육정지 — 보온 필수.",
        "heatwave":   "수정 불량(공동과·기형과) — 차광·관수.",
        "fungal_humidity": "잎곰팡이병/노균병 호조 — 환기 강화.",
        "drought":    "비대기 수분 부족 시 배꼽썩음과 급증 — 칼슘+관수 보강.",
        "snow":       "비닐하우스 적설하중 — 측창 보온재 정리, 야간 난방 가동·환기로 적설 미끄러뜨리기.",
        "temp_swing": "큰 일교차는 갈라짐·기형과·배꼽썩음 빈발 — 야간 보온·점적 안정화.",
    },
    "오이": {
        "frost":      "냉해에 매우 취약 — 야간 보온 필수.",
        "fungal_humidity": "노균병 폭발 위험 — 즉시 환기.",
        "drought":    "수분 결핍 시 곡과·낙과 — 관수 빈도 상향.",
        "snow":       "비닐하우스 적설하중 — 난방 가동·물뿌리기로 지붕 적설 차단.",
        "temp_swing": "큰 일교차는 곡과·낙화 — 시설 야간 보온 강화.",
    },
    "딸기": {
        "frost":      "관부 동해 위험 — 부직포·수막재배 점검.",
        "heatwave":   "꽃눈 분화 저해 — 차광·환기.",
        "fungal_humidity": "잿빛곰팡이병 위험 — 환기·약제 사이클 단축.",
        "snow":       "하우스 측면 적설 정리, 수막재배 동결 주의 — 야간 가동 점검.",
        "temp_swing": "큰 일교차는 기형과·열과 — 야간 보온·환기 균형.",
    },
    "고추": {
        "frost":      "정식 직후 냉해 치명적 — 보온 터널.",
        "strong_wind": "쓰러짐·가지 부러짐 — 지주 보강.",
        "heavy_rain": "역병/탄저 폭발 위험 — 배수로 점검.",
        "drought":    "착과기 가뭄 시 낙화·낙과 — 점적관수 가동.",
        "snow":       "노지 월동 시 멀칭·부직포 보완. 시설재배는 적설하중 점검.",
        "temp_swing": "착과기 큰 일교차는 낙화·낙과 — 야간 보온, 시설은 변온관리.",
    },
    "벼": {
        "strong_wind": "출수기 도복 위험 — 물 빼기·낙수 검토.",
        "cold_stress": "야간 17℃ 이하 지속 시 냉해 가능 (출수~등숙기).",
        "heavy_rain": "도복·관수 피해 — 즉시 배수.",
        "drought":    "이앙·활착기 무강수는 분얼 저해 — 담수 깊이 점검.",
        "temp_swing": "등숙기 큰 일교차는 야간 저온으로 등숙률 저하 — 담수 깊이로 야간 보온.",
    },
    "배추": {
        "frost":      "결구기 동해 — 부직포 점검.",
        "strong_wind": "잎 찢김·뿌리 흔들림.",
        "drought":    "결구기 가뭄 시 결구 부진·열구 — 관수 보강.",
        "snow":       "결구기 적설은 결구부 동상해 — 수확 임박 포기는 즉시 수확 검토.",
        "temp_swing": "정식 직후 큰 일교차는 추대(꽃대) 가속 위험 — 보온·정식 시기 검토.",
    },
    "마늘": {
        "frost":      "월동 직후 발아 시 동해 — 멀칭 점검.",
        "drought":    "구비대기 수분 부족 시 인편 발달 저해.",
        "snow":       "월동기 적설은 보온 효과도 있으나, 융설 시 침수·습해 주의 — 배수로 점검.",
    },
    "양파": {
        "heavy_rain": "수확기 비는 부패·저장성 저하.",
        "drought":    "비대기(4~5월) 가뭄은 구 크기 직격 — 점적관수.",
        "snow":       "월동기 멀칭·부직포 점검. 융설 후 습해로 인한 무름병 주의.",
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
    horizon_days: int = 3,
) -> list[dict[str, Any]]:
    """Build a list of crop-aware risk advisories from a get_weather() payload.

    Args:
        weather: dict shaped like get_weather() output. Tolerant of missing
            keys — empty input → empty list (callers safely chain).
        main_crop: free-form Korean crop name. Used only for crop_hint
            decoration; thresholds themselves are crop-agnostic.
        horizon_days: how many `daily_forecasts` entries to scan (default 3).

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
        offset = entry.get("day_offset")
        offset = int(offset) if isinstance(offset, int) else None
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
