"""기상청 API 클라이언트 — 실제 API 또는 mock 데이터 제공.

KMA 다층 예보 시스템:
  1. 초단기실황 (getUltraSrtNcst)  — 현재 관측값 (10분 갱신)
  2. 초단기예보 (getUltraSrtFcst)  — 향후 ~6시간, 1시간 간격
  3. 단기예보   (getVilageFcst)     — 향후 ~3일, 3시간 간격
  4. 중기예보   (getMidLandFcst + getMidTa) — 3~7일 후, 일 단위

본 모듈은 위 4개를 조합해 다음을 노출:
  - current               (#1)
  - forecasts             (#2 → 시간별, 향후 6h)
  - daily_forecasts       (#3 일 집계 + #4) — **5일치 일일 요약**
"""

import math
import random
from datetime import date as date_cls, datetime, time, timezone, timedelta

import httpx

from app.core.config import settings

# 캐시 (10분 TTL)
_cache: dict = {}
_cache_expiry: datetime | None = None
CACHE_TTL = timedelta(minutes=10)

KST = timezone(timedelta(hours=9))


def _latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    """위경도 → 기상청 격자좌표 변환 (Lambert Conformal Conic)."""
    RE = 6371.00877
    GRID = 5.0
    SLAT1 = 30.0
    SLAT2 = 60.0
    OLON = 126.0
    OLAT = 38.0
    XO = 43
    YO = 136

    DEGRAD = math.pi / 180.0
    re = RE / GRID
    slat1 = SLAT1 * DEGRAD
    slat2 = SLAT2 * DEGRAD
    olon = OLON * DEGRAD
    olat = OLAT * DEGRAD

    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = (sf ** sn) * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re * sf / (ro ** sn)

    ra = math.tan(math.pi * 0.25 + lat * DEGRAD * 0.5)
    ra = re * sf / (ra ** sn)
    theta = lon * DEGRAD - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    x = int(ra * math.sin(theta) + XO + 0.5)
    y = int(ro - ra * math.cos(theta) + YO + 0.5)
    return x, y


def _now_kst() -> datetime:
    return datetime.now(KST)


def _get_base_datetime() -> tuple[str, str]:
    """초단기 API 기준시각 산출.

    KMA data is published after the base hour. Waiting 45 minutes avoids asking
    for a not-yet-published hour and keeps the date correct around midnight.
    """
    now = datetime.now(KST)
    if now.minute < 45:
        now -= timedelta(hours=1)
    return now.strftime("%Y%m%d"), now.strftime("%H00")


# 단기예보(getVilageFcst) 발표 시각: 02, 05, 08, 11, 14, 17, 20, 23 (1일 8회)
_VILLAGE_BASE_HOURS = [2, 5, 8, 11, 14, 17, 20, 23]


def _get_village_base_datetime() -> tuple[str, str]:
    """단기예보 기준 발표시각 — 위 8개 시각 중 가장 최근 것 (10분 후 조회 가능 가정)."""
    now = datetime.now(KST)
    for hour in reversed(_VILLAGE_BASE_HOURS):
        if now.hour > hour or (now.hour == hour and now.minute >= 10):
            return now.strftime("%Y%m%d"), f"{hour:02d}00"
    # 02:10 이전 — 전날 23:00 발표
    prev = now - timedelta(days=1)
    return prev.strftime("%Y%m%d"), "2300"


def _get_mid_term_tmfc() -> str:
    """중기예보(getMidLandFcst, getMidTa) 발표시각 — 06:00 또는 18:00 KST.

    KMA가 06/18 정시에 발표하며, 30분 정도 지연이 있어 "10분 이후" 가드를 둔다.
    반환 형식: 'YYYYMMDDHHMM'.
    """
    now = datetime.now(KST)
    if now.hour > 18 or (now.hour == 18 and now.minute >= 10):
        return now.strftime("%Y%m%d") + "1800"
    if now.hour > 6 or (now.hour == 6 and now.minute >= 10):
        return now.strftime("%Y%m%d") + "0600"
    # 06:10 이전 → 전날 18:00 발표 사용
    prev = now - timedelta(days=1)
    return prev.strftime("%Y%m%d") + "1800"


def _parse_kma_datetime(date_value: str, time_value: str) -> datetime:
    """Parse KMA YYYYMMDD + HHMM into a timezone-aware KST datetime."""
    hour = int(time_value[:2])
    minute = int(time_value[2:4])
    base = datetime.strptime(date_value, "%Y%m%d").date()
    if hour == 24:
        base += timedelta(days=1)
        hour = 0
    return datetime.combine(base, time(hour=hour, minute=minute), tzinfo=KST)


def _format_kma_date(date_value: str) -> str:
    return datetime.strptime(date_value, "%Y%m%d").date().isoformat()


def _format_kma_time(time_value: str) -> str:
    return f"{time_value[:2]}:{time_value[2:4]}"


_SKY_MAP = {"1": "맑음", "3": "구름많음", "4": "흐림"}
_PTY_MAP = {
    "0": "없음",
    "1": "비",
    "2": "비/눈",
    "3": "눈",
    "5": "빗방울",
    "6": "진눈깨비",
    "7": "눈날림",
}


def _resolve_sky_pty(sky: str | int | None, pty: str | int | None) -> str:
    """SKY+PTY 코드를 사용자 친화적 한 단어로 변환 (강수 우선)."""
    pty_str = str(pty) if pty is not None else "0"
    if pty_str != "0":
        return _PTY_MAP.get(pty_str, "비")
    return _SKY_MAP.get(str(sky) if sky is not None else "1", "맑음")


async def _fetch_kma_ultra_srt_ncst() -> dict | None:
    """기상청 초단기실황 API 호출."""
    if not settings.KMA_DECODING_KEY:
        return None

    base_date, base_time = _get_base_datetime()
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
    params = {
        "serviceKey": settings.KMA_DECODING_KEY,
        "numOfRows": 10,
        "pageNo": 1,
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": settings.FARM_NX,
        "ny": settings.FARM_NY,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])
            if not items:
                return None

            result = {}
            for item in items:
                cat = item["category"]
                val = item["obsrValue"]
                if cat == "T1H":
                    result["temperature"] = float(val)
                elif cat == "REH":
                    result["humidity"] = int(float(val))
                elif cat == "WSD":
                    result["wind_speed"] = float(val)
                elif cat == "VEC":
                    result["wind_direction"] = int(float(val))
                elif cat == "RN1":
                    result["precipitation"] = float(val)
                elif cat == "PTY":
                    result["precipitation_type"] = _PTY_MAP.get(val, "없음")

            first = items[0]
            item_base_date = first.get("baseDate", base_date)
            item_base_time = first.get("baseTime", base_time)
            observed_at = _parse_kma_datetime(item_base_date, item_base_time)
            result.update({
                "base_date": _format_kma_date(item_base_date),
                "base_time": _format_kma_time(item_base_time),
                "observed_at": observed_at.isoformat(),
            })
            return result
    except Exception:
        return None


async def _fetch_kma_ultra_srt_fcst() -> list[dict] | None:
    """기상청 초단기예보 API 호출 (향후 ~6시간, 시간 단위)."""
    if not settings.KMA_DECODING_KEY:
        return None

    base_date, base_time = _get_base_datetime()
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtFcst"
    params = {
        "serviceKey": settings.KMA_DECODING_KEY,
        "numOfRows": 60,
        "pageNo": 1,
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": settings.FARM_NX,
        "ny": settings.FARM_NY,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])
            if not items:
                return None

            forecasts: dict[str, dict] = {}
            for item in items:
                key = f"{item['fcstDate']}_{item['fcstTime']}"
                if key not in forecasts:
                    forecast_at = _parse_kma_datetime(item["fcstDate"], item["fcstTime"])
                    forecasts[key] = {
                        "date": forecast_at.date().isoformat(),
                        "time": forecast_at.strftime("%H:%M"),
                        "valid_at": forecast_at.isoformat(),
                    }
                cat = item["category"]
                val = item["fcstValue"]
                if cat == "T1H":
                    forecasts[key]["temperature"] = float(val)
                elif cat == "REH":
                    forecasts[key]["humidity"] = int(float(val))
                elif cat == "WSD":
                    forecasts[key]["wind_speed"] = float(val)
                elif cat == "SKY":
                    forecasts[key]["sky_code"] = val
                    forecasts[key]["sky"] = _SKY_MAP.get(val, "맑음")
                elif cat == "PTY":
                    forecasts[key]["pty_code"] = val
                elif cat == "RN1":
                    forecasts[key]["precipitation"] = (
                        float(val) if val not in ("강수없음", "-") else 0.0
                    )

            return sorted(forecasts.values(), key=lambda item: item["valid_at"])
    except Exception:
        return None


async def _fetch_kma_vilage_fcst() -> list[dict] | None:
    """기상청 단기예보 API 호출 (향후 ~3일, 3시간 간격).

    시간별 항목 리스트 반환 — 일 집계는 호출자가 수행.
    """
    if not settings.KMA_DECODING_KEY:
        return None

    base_date, base_time = _get_village_base_datetime()
    url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    params = {
        "serviceKey": settings.KMA_DECODING_KEY,
        "numOfRows": 1000,
        "pageNo": 1,
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": settings.FARM_NX,
        "ny": settings.FARM_NY,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])
            if not items:
                return None

            forecasts: dict[str, dict] = {}
            for item in items:
                key = f"{item['fcstDate']}_{item['fcstTime']}"
                if key not in forecasts:
                    forecast_at = _parse_kma_datetime(item["fcstDate"], item["fcstTime"])
                    forecasts[key] = {
                        "date": forecast_at.date().isoformat(),
                        "time": forecast_at.strftime("%H:%M"),
                        "valid_at": forecast_at.isoformat(),
                    }
                cat = item["category"]
                val = item["fcstValue"]
                # 단기예보 카테고리: TMP, TMN, TMX, REH, WSD, SKY, PTY, POP, PCP, SNO
                if cat == "TMP":
                    forecasts[key]["temperature"] = float(val)
                elif cat == "TMN":
                    forecasts[key]["temp_min"] = float(str(val).replace("℃", ""))
                elif cat == "TMX":
                    forecasts[key]["temp_max"] = float(str(val).replace("℃", ""))
                elif cat == "REH":
                    forecasts[key]["humidity"] = int(float(val))
                elif cat == "WSD":
                    forecasts[key]["wind_speed"] = float(val)
                elif cat == "SKY":
                    forecasts[key]["sky_code"] = val
                    forecasts[key]["sky"] = _SKY_MAP.get(val, "맑음")
                elif cat == "PTY":
                    forecasts[key]["pty_code"] = val
                elif cat == "POP":
                    forecasts[key]["precipitation_prob"] = int(float(val))
                elif cat == "PCP":
                    # "강수없음" / "1mm 미만" / "1.0mm" — 숫자만 추출
                    if val in ("강수없음", "0", "0.0", "-"):
                        forecasts[key]["precipitation"] = 0.0
                    elif "미만" in str(val):
                        forecasts[key]["precipitation"] = 0.5
                    else:
                        try:
                            forecasts[key]["precipitation"] = float(
                                str(val).replace("mm", "").strip()
                            )
                        except ValueError:
                            forecasts[key]["precipitation"] = 0.0

            return sorted(forecasts.values(), key=lambda i: i["valid_at"])
    except Exception:
        return None


async def _fetch_kma_mid_land_fcst() -> dict | None:
    """기상청 중기육상예보 API — 3~7일 후 SKY/POP. 일자별 dict 반환."""
    if not settings.KMA_DECODING_KEY or not settings.KMA_MID_LAND_REG_ID:
        return None

    tmfc = _get_mid_term_tmfc()
    url = "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst"
    params = {
        "serviceKey": settings.KMA_DECODING_KEY,
        "numOfRows": 10,
        "pageNo": 1,
        "dataType": "JSON",
        "regId": settings.KMA_MID_LAND_REG_ID,
        "tmFc": tmfc,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])
            if not items:
                return None
            row = items[0]
            # 응답 키 패턴: rnSt3Am, rnSt3Pm, rnSt4, rnSt5, ... wf3Am, wf3Pm, wf4, ...
            today_kst = datetime.now(KST).date()
            result: dict[str, dict] = {}
            for offset in range(3, 8):
                target = (today_kst + timedelta(days=offset)).isoformat()
                rn_am = row.get(f"rnSt{offset}Am")
                rn_pm = row.get(f"rnSt{offset}Pm")
                rn = row.get(f"rnSt{offset}")
                wf_am = row.get(f"wf{offset}Am")
                wf_pm = row.get(f"wf{offset}Pm")
                wf = row.get(f"wf{offset}")
                # AM/PM 분할 데이터(3~7일)와 단일(8일+)을 통합 처리
                rain_prob = max(
                    [int(v) for v in (rn_am, rn_pm, rn) if v is not None and str(v).isdigit()],
                    default=0,
                )
                sky_label = wf_am or wf_pm or wf or ""
                result[target] = {
                    "date": target,
                    "precipitation_prob": rain_prob,
                    "sky": sky_label or "확인 어려움",
                }
            return result
    except Exception:
        return None


async def _fetch_kma_mid_ta() -> dict | None:
    """기상청 중기기온 API — 3~7일 후 최저/최고 기온. 일자별 dict 반환."""
    if not settings.KMA_DECODING_KEY or not settings.KMA_MID_TEMP_REG_ID:
        return None

    tmfc = _get_mid_term_tmfc()
    url = "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa"
    params = {
        "serviceKey": settings.KMA_DECODING_KEY,
        "numOfRows": 10,
        "pageNo": 1,
        "dataType": "JSON",
        "regId": settings.KMA_MID_TEMP_REG_ID,
        "tmFc": tmfc,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])
            if not items:
                return None
            row = items[0]
            today_kst = datetime.now(KST).date()
            result: dict[str, dict] = {}
            for offset in range(3, 8):
                target = (today_kst + timedelta(days=offset)).isoformat()
                t_min = row.get(f"taMin{offset}")
                t_max = row.get(f"taMax{offset}")
                if t_min is None and t_max is None:
                    continue
                result[target] = {
                    "date": target,
                    "temp_min": float(t_min) if t_min is not None else None,
                    "temp_max": float(t_max) if t_max is not None else None,
                }
            return result
    except Exception:
        return None


def _aggregate_to_daily(items: list[dict]) -> dict[str, dict]:
    """단기예보 시간별 → 일자별 요약 집계.

    각 일자에 대해 min/max 기온, 최대 강수확률, 합계 강수량, 우세 sky/pty 산출.
    sky 우선순위: 강수(PTY!=0) > 흐림 > 구름많음 > 맑음.
    """
    daily: dict[str, dict] = {}
    pty_priority = {"1", "2", "3", "5", "6", "7"}  # any of these → 강수
    sky_rank = {"1": 0, "3": 1, "4": 2}  # 맑음 < 구름많음 < 흐림

    for item in items:
        d = item.get("date")
        if not d:
            continue
        bucket = daily.setdefault(d, {
            "date": d,
            "temps": [],
            "humidities": [],
            "winds": [],
            "precip_probs": [],
            "precip_total": 0.0,
            "sky_codes": [],
            "pty_codes": [],
            "tmn": None,
            "tmx": None,
        })
        if "temperature" in item:
            bucket["temps"].append(item["temperature"])
        if "temp_min" in item and bucket["tmn"] is None:
            bucket["tmn"] = item["temp_min"]
        if "temp_max" in item and bucket["tmx"] is None:
            bucket["tmx"] = item["temp_max"]
        if "humidity" in item:
            bucket["humidities"].append(item["humidity"])
        if "wind_speed" in item:
            bucket["winds"].append(item["wind_speed"])
        if "precipitation_prob" in item:
            bucket["precip_probs"].append(item["precipitation_prob"])
        if "precipitation" in item:
            bucket["precip_total"] += float(item["precipitation"])
        if "sky_code" in item:
            bucket["sky_codes"].append(str(item["sky_code"]))
        if "pty_code" in item:
            bucket["pty_codes"].append(str(item["pty_code"]))

    summary: dict[str, dict] = {}
    for d, b in daily.items():
        # 우세 sky/pty 결정
        rainy_pty = next((p for p in b["pty_codes"] if p in pty_priority), None)
        if rainy_pty:
            sky_label = _PTY_MAP.get(rainy_pty, "비")
        else:
            # 가장 흐린 sky
            worst = max(b["sky_codes"], key=lambda c: sky_rank.get(c, 0), default="1")
            sky_label = _SKY_MAP.get(worst, "맑음")
        summary[d] = {
            "date": d,
            "temp_min": b["tmn"] if b["tmn"] is not None else (min(b["temps"]) if b["temps"] else None),
            "temp_max": b["tmx"] if b["tmx"] is not None else (max(b["temps"]) if b["temps"] else None),
            "humidity_avg": (
                round(sum(b["humidities"]) / len(b["humidities"])) if b["humidities"] else None
            ),
            "wind_speed_max": round(max(b["winds"]), 1) if b["winds"] else None,
            "precipitation_prob": max(b["precip_probs"], default=0),
            "precipitation": round(b["precip_total"], 1),
            "sky": sky_label,
        }
    return summary


def _build_daily_forecasts(
    village_items: list[dict] | None,
    mid_land: dict | None,
    mid_ta: dict | None,
    days: int = 5,
) -> list[dict]:
    """단기예보(0~3일) + 중기예보(3~7일)를 일 단위로 결합해 5일치 반환.

    village_items: getVilageFcst 시간별 배열
    mid_land: { 'YYYY-MM-DD': {sky, precipitation_prob} }
    mid_ta:   { 'YYYY-MM-DD': {temp_min, temp_max} }
    """
    today = datetime.now(KST).date()
    short = _aggregate_to_daily(village_items or [])

    output: list[dict] = []
    for offset in range(days):
        target_date = (today + timedelta(days=offset)).isoformat()
        if target_date in short:
            entry = short[target_date]
        elif mid_ta and target_date in mid_ta:
            ta = mid_ta[target_date]
            land = (mid_land or {}).get(target_date) or {}
            entry = {
                "date": target_date,
                "temp_min": ta.get("temp_min"),
                "temp_max": ta.get("temp_max"),
                "humidity_avg": None,
                "wind_speed_max": None,
                "precipitation_prob": land.get("precipitation_prob", 0),
                "precipitation": None,
                "sky": land.get("sky", "확인 어려움"),
            }
        else:
            # 데이터 부재 — 자리표시
            entry = {
                "date": target_date,
                "temp_min": None,
                "temp_max": None,
                "humidity_avg": None,
                "wind_speed_max": None,
                "precipitation_prob": None,
                "precipitation": None,
                "sky": "확인 어려움",
            }
        # 요일 라벨 추가
        weekday = ["월", "화", "수", "목", "금", "토", "일"][
            date_cls.fromisoformat(target_date).weekday()
        ]
        entry["weekday"] = weekday
        entry["day_offset"] = offset
        output.append(entry)
    return output


def _generate_mock_weather(sensor_data: dict | None = None) -> dict:
    """센서 데이터 기반 mock 기상 데이터 생성."""
    now = datetime.now(KST)
    kst_hour = now.hour

    if sensor_data:
        base_temp = sensor_data.get("temperature", 22) - random.uniform(1, 4)
        base_humidity = max(30, sensor_data.get("humidity", 60) - random.uniform(5, 15))
    else:
        base_temp = 18 + random.uniform(-3, 5)
        base_humidity = 55 + random.uniform(-10, 15)

    current = {
        "temperature": round(base_temp, 1),
        "humidity": int(base_humidity),
        "wind_speed": round(random.uniform(1.0, 6.0), 1),
        "wind_direction": random.randint(0, 360),
        "precipitation": 0.0,
        "precipitation_type": "없음",
    }

    # 시간대별 예보 생성 (3, 6, 12시간 후)
    forecasts = []
    for hours_ahead in [3, 6, 12]:
        future_hour = (kst_hour + hours_ahead) % 24
        temp_shift = -2 if future_hour < 6 or future_hour > 20 else 2
        valid_at = now + timedelta(hours=hours_ahead)
        forecasts.append({
            "hours_ahead": hours_ahead,
            "date": valid_at.date().isoformat(),
            "time": valid_at.strftime("%H:%M"),
            "valid_at": valid_at.isoformat(),
            "temperature": round(base_temp + temp_shift + random.uniform(-1, 1), 1),
            "humidity": int(min(95, max(30, base_humidity + random.uniform(-10, 10)))),
            "wind_speed": round(random.uniform(1.0, 6.0), 1),
            "sky": random.choice(["맑음", "구름많음", "흐림"]),
            "precipitation_prob": random.choice([0, 0, 0, 10, 20, 30]),
            "precipitation": 0.0,
        })

    # mock 5일 일일 예보
    today = now.date()
    daily_forecasts = []
    for offset in range(5):
        target = today + timedelta(days=offset)
        weekday = ["월", "화", "수", "목", "금", "토", "일"][target.weekday()]
        sky = random.choice(["맑음", "구름많음", "흐림", "비"])
        daily_forecasts.append({
            "date": target.isoformat(),
            "weekday": weekday,
            "day_offset": offset,
            "temp_min": round(base_temp - 4 + random.uniform(-2, 2), 1),
            "temp_max": round(base_temp + 4 + random.uniform(-2, 2), 1),
            "humidity_avg": int(base_humidity + random.uniform(-5, 5)),
            "wind_speed_max": round(random.uniform(2.0, 7.0), 1),
            "precipitation_prob": 0 if sky == "맑음" else random.choice([10, 20, 30, 50, 70]),
            "precipitation": 0.0 if sky != "비" else round(random.uniform(0.5, 8.0), 1),
            "sky": sky,
        })

    current["observed_at"] = now.isoformat()
    return {
        "current": current,
        "forecasts": forecasts,
        "daily_forecasts": daily_forecasts,
        "source": "mock",
        "timezone": "Asia/Seoul",
        "generated_at": now.isoformat(),
        "nx": settings.FARM_NX,
        "ny": settings.FARM_NY,
    }


async def get_weather(sensor_data: dict | None = None) -> dict:
    """기상 데이터를 반환한다. KMA API 키가 있으면 실제 호출, 없으면 mock.

    Returns:
        {
            "current":         { temperature, humidity, wind_speed, ... },
            "forecasts":       [ 시간별 (~6h) ],
            "daily_forecasts": [ 일별 5일 — date, temp_min/max, sky, precip_prob, ... ],
            "source": "kma" | "mock",
            "timezone": "Asia/Seoul",
            ...
        }
    """
    global _cache, _cache_expiry

    # 캐시 확인
    now_utc = datetime.now(timezone.utc)
    generated_at = _now_kst()
    if _cache_expiry and now_utc < _cache_expiry and _cache:
        return _cache

    # KMA API 시도
    if settings.KMA_DECODING_KEY:
        current = await _fetch_kma_ultra_srt_ncst()
        # 시간별(6h) + 단기(3일) + 중기(3~7일)를 병렬로 수집
        ultra_short_items = await _fetch_kma_ultra_srt_fcst()
        village_items = await _fetch_kma_vilage_fcst()
        mid_land = await _fetch_kma_mid_land_fcst()
        mid_ta = await _fetch_kma_mid_ta()

        if current:
            forecasts = []
            if ultra_short_items:
                for item in ultra_short_items[:6]:
                    valid_at = datetime.fromisoformat(item["valid_at"])
                    hours_ahead = max(
                        0,
                        round((valid_at - generated_at).total_seconds() / 3600),
                    )
                    forecasts.append({
                        "hours_ahead": hours_ahead,
                        "date": item.get("date"),
                        "time": item.get("time"),
                        "valid_at": item.get("valid_at"),
                        "temperature": item.get("temperature", current["temperature"]),
                        "humidity": item.get("humidity", current["humidity"]),
                        "wind_speed": item.get("wind_speed", current.get("wind_speed", 2.0)),
                        "sky": item.get("sky", "맑음"),
                        "precipitation_prob": 0,
                        "precipitation": item.get("precipitation", 0.0),
                    })

            daily_forecasts = _build_daily_forecasts(
                village_items=village_items,
                mid_land=mid_land,
                mid_ta=mid_ta,
                days=5,
            )

            result = {
                "current": current,
                "forecasts": forecasts,
                "daily_forecasts": daily_forecasts,
                "source": "kma",
                "timezone": "Asia/Seoul",
                "generated_at": generated_at.isoformat(),
                "nx": settings.FARM_NX,
                "ny": settings.FARM_NY,
            }
            _cache = result
            _cache_expiry = now_utc + CACHE_TTL
            return result

    # Mock 폴백
    result = _generate_mock_weather(sensor_data)
    _cache = result
    _cache_expiry = now_utc + CACHE_TTL
    return result
