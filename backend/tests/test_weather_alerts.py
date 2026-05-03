"""Tests for the deterministic weather risk advisory.

Pure unit tests: no DB, no network, no LLM. Runs cleanly under pytest if it's
installed; the module also has a `__main__` self-runner so it works without
pytest (`uv run python -m tests.test_weather_alerts`).
"""

from __future__ import annotations

from app.services.farm_agent.weather_alerts import (
    analyze_weather_risks,
    format_advisories_markdown,
)


def _daily(
    *,
    offset: int = 0,
    tmin: float | None = None,
    tmax: float | None = None,
    wmax: float | None = None,
    precip: float = 0.0,
    pprob: float | None = None,
    humidity: float | None = None,
    sky: str = "맑음",
) -> dict:
    """Build a minimal daily_forecasts entry matching get_weather() shape."""
    return {
        "day_offset": offset,
        "date": f"2026-05-0{offset + 1}",
        "temp_min": tmin,
        "temp_max": tmax,
        "wind_speed_max": wmax,
        "precipitation": precip,
        "precipitation_prob": pprob,
        "humidity_avg": humidity,
        "sky": sky,
    }


def test_empty_input_returns_empty_list():
    assert analyze_weather_risks(None) == []
    assert analyze_weather_risks({}) == []
    assert analyze_weather_risks({"daily_forecasts": []}) == []


def test_no_thresholds_tripped_means_no_advisories():
    weather = {
        "current": {"temperature": 20, "wind_speed": 2.0, "precipitation_type": "없음"},
        "daily_forecasts": [
            _daily(offset=0, tmin=12, tmax=24, wmax=4.0, precip=0.0, humidity=60),
            _daily(offset=1, tmin=13, tmax=25, wmax=3.5, precip=0.0, humidity=58),
        ],
    }
    assert analyze_weather_risks(weather) == []


def test_frost_critical_vs_warning():
    weather = {
        "daily_forecasts": [
            _daily(offset=1, tmin=1.5, tmax=12),     # warning (≤2)
            _daily(offset=2, tmin=-3.0, tmax=8),     # critical (≤-2)
        ],
    }
    out = analyze_weather_risks(weather)
    kinds = {(a["kind"], a["level"]) for a in out}
    assert ("frost", "warning") in kinds
    assert ("frost", "critical") in kinds


def test_heatwave_extreme_promotes_to_critical():
    weather = {
        "daily_forecasts": [
            _daily(offset=0, tmax=34, tmin=20),   # warning (33-35)
            _daily(offset=1, tmax=36, tmin=24),   # critical (≥35)
        ],
    }
    out = analyze_weather_risks(weather)
    levels = {a["when"]: a["level"] for a in out if a["kind"] == "heatwave"}
    assert levels.get("오늘") == "warning"
    assert levels.get("내일") == "critical"


def test_heavy_rain_uses_measured_value_over_probability():
    weather = {
        "daily_forecasts": [
            # Big precip overrides anything else.
            _daily(offset=0, precip=85, pprob=90, tmax=22, tmin=18),
        ],
    }
    out = analyze_weather_risks(weather)
    rain = [a for a in out if a["kind"] == "heavy_rain"]
    assert rain, "heavy_rain advisory expected"
    assert rain[0]["level"] == "critical"  # ≥80mm


def test_high_prob_without_volume_emits_info_rain_likely():
    weather = {
        "daily_forecasts": [
            _daily(offset=1, precip=2, pprob=80, tmax=22, tmin=15),
        ],
    }
    out = analyze_weather_risks(weather)
    kinds = {a["kind"] for a in out}
    assert "rain_likely" in kinds
    assert "heavy_rain" not in kinds


def test_strong_wind_current_and_forecast():
    weather = {
        "current": {"wind_speed": 15.0},  # ≥14 → critical
        "daily_forecasts": [_daily(offset=1, wmax=10.0, tmax=20, tmin=12)],  # ≥9 → warning
    }
    out = analyze_weather_risks(weather)
    by_when = {(a["when"], a["kind"]): a["level"] for a in out}
    assert by_when.get(("지금", "strong_wind")) == "critical"
    assert by_when.get(("내일", "strong_wind")) == "warning"


def test_fungal_humidity_window_only_in_temperate_band():
    # 17℃ + 90% humidity → fungal_humidity advisory.
    in_band = {
        "daily_forecasts": [
            _daily(offset=0, tmax=18, tmin=14, humidity=90),
        ],
    }
    out_in = {a["kind"] for a in analyze_weather_risks(in_band)}
    assert "fungal_humidity" in out_in

    # 30℃ + 90% → outside fungal-friendly temp band, but heatwave still fires.
    out_band = {
        "daily_forecasts": [
            _daily(offset=0, tmax=34, tmin=24, humidity=90),
        ],
    }
    kinds = {a["kind"] for a in analyze_weather_risks(out_band)}
    assert "fungal_humidity" not in kinds
    assert "heatwave" in kinds


def test_crop_hint_attaches_for_known_crop():
    weather = {
        "daily_forecasts": [_daily(offset=0, tmin=0.5, tmax=10)],
    }
    out = analyze_weather_risks(weather, main_crop="사과")
    frost = next(a for a in out if a["kind"] == "frost")
    assert frost["crop_hint"] and "개화기" in frost["crop_hint"]


def test_unknown_crop_leaves_hint_none():
    weather = {
        "daily_forecasts": [_daily(offset=0, tmin=0.5, tmax=10)],
    }
    out = analyze_weather_risks(weather, main_crop="이름없는작물")
    frost = next(a for a in out if a["kind"] == "frost")
    assert frost["crop_hint"] is None


def test_sort_order_critical_first():
    weather = {
        "current": {"wind_speed": 1.0, "temperature": 20, "precipitation_type": "없음"},
        "daily_forecasts": [
            _daily(offset=0, tmin=15, tmax=24, pprob=80, precip=2),    # info
            _daily(offset=1, tmin=-3, tmax=8),                          # critical frost
            _daily(offset=2, tmin=18, tmax=34),                         # warning heatwave
        ],
    }
    out = analyze_weather_risks(weather)
    levels = [a["level"] for a in out]
    # All criticals come before warnings come before infos.
    rank = {"critical": 0, "warning": 1, "info": 2}
    assert levels == sorted(levels, key=lambda lv: rank.get(lv, 9))


def test_format_markdown_empty_input_has_safe_placeholder():
    out = format_advisories_markdown([])
    assert "특이사항 없음" in out
    assert out.startswith("## ⚠️")


def test_drought_warning_at_5_consecutive_dry_days():
    weather = {
        "current": {"precipitation_type": "없음", "precipitation": 0},
        "daily_forecasts": [
            _daily(offset=i, tmin=14, tmax=24, precip=0.0, humidity=55)
            for i in range(5)
        ],
    }
    out = analyze_weather_risks(weather)
    drought = [a for a in out if a["kind"] == "drought"]
    assert drought, "drought advisory expected"
    assert drought[0]["level"] == "warning"
    assert drought[0]["value"] == 5


def test_drought_critical_at_7_consecutive_dry_days():
    weather = {
        "current": {"precipitation_type": "없음", "precipitation": 0},
        "daily_forecasts": [
            _daily(offset=i, tmin=14, tmax=24, precip=0.0, humidity=55)
            for i in range(7)
        ],
    }
    out = analyze_weather_risks(weather)
    drought = [a for a in out if a["kind"] == "drought"]
    assert drought and drought[0]["level"] == "critical"
    assert drought[0]["value"] == 7


def test_drought_below_threshold_emits_nothing():
    weather = {
        "current": {"precipitation_type": "없음", "precipitation": 0},
        "daily_forecasts": [
            _daily(offset=i, tmin=14, tmax=24, precip=0.0, humidity=55)
            for i in range(4)  # only 4 days dry
        ],
    }
    out = analyze_weather_risks(weather)
    assert not [a for a in out if a["kind"] == "drought"]


def test_drought_streak_breaks_on_rainy_day():
    weather = {
        "current": {"precipitation_type": "없음", "precipitation": 0},
        "daily_forecasts": [
            _daily(offset=0, tmin=14, tmax=24, precip=0.0),
            _daily(offset=1, tmin=14, tmax=24, precip=0.0),
            _daily(offset=2, tmin=14, tmax=24, precip=8.0),  # rain breaks streak
            _daily(offset=3, tmin=14, tmax=24, precip=0.0),
            _daily(offset=4, tmin=14, tmax=24, precip=0.0),
        ],
    }
    out = analyze_weather_risks(weather)
    assert not [a for a in out if a["kind"] == "drought"]


def test_drought_suppressed_when_currently_raining():
    weather = {
        "current": {"precipitation_type": "비", "precipitation": 5.0},
        "daily_forecasts": [
            _daily(offset=i, tmin=14, tmax=24, precip=0.0) for i in range(7)
        ],
    }
    out = analyze_weather_risks(weather)
    assert not [a for a in out if a["kind"] == "drought"]


def test_drought_missing_precip_stops_count_conservatively():
    # 결측 항목을 "건조" 로 카운트하지 않는다 — 4일 건조 + 결측 → no advisory.
    weather = {
        "current": {"precipitation_type": "없음", "precipitation": 0},
        "daily_forecasts": [
            _daily(offset=0, tmin=14, tmax=24, precip=0.0),
            _daily(offset=1, tmin=14, tmax=24, precip=0.0),
            _daily(offset=2, tmin=14, tmax=24, precip=0.0),
            _daily(offset=3, tmin=14, tmax=24, precip=0.0),
            {"day_offset": 4, "temp_min": 14, "temp_max": 24},  # precip missing
            _daily(offset=5, tmin=14, tmax=24, precip=0.0),
        ],
    }
    out = analyze_weather_risks(weather)
    assert not [a for a in out if a["kind"] == "drought"]


def test_drought_attaches_crop_hint_for_known_crop():
    weather = {
        "current": {"precipitation_type": "없음", "precipitation": 0},
        "daily_forecasts": [
            _daily(offset=i, tmin=14, tmax=24, precip=0.0) for i in range(6)
        ],
    }
    out = analyze_weather_risks(weather, main_crop="토마토")
    drought = next(a for a in out if a["kind"] == "drought")
    assert drought["crop_hint"] and "배꼽썩음" in drought["crop_hint"]


def test_drought_advisory_keeps_severity_sort_order():
    # critical drought 는 warning frost 보다 앞에, info 보다 뒤에 위치.
    weather = {
        "current": {"precipitation_type": "없음", "precipitation": 0},
        "daily_forecasts": [
            _daily(offset=0, tmin=1.5, tmax=20, precip=0.0),  # warning frost (critical 아님)
            _daily(offset=1, tmin=14, tmax=24, precip=0.0),
            _daily(offset=2, tmin=14, tmax=24, precip=0.0),
            _daily(offset=3, tmin=14, tmax=24, precip=0.0),
            _daily(offset=4, tmin=14, tmax=24, precip=0.0),
            _daily(offset=5, tmin=14, tmax=24, precip=0.0),
            _daily(offset=6, tmin=14, tmax=24, precip=0.0),  # 7-day dry → critical drought
        ],
    }
    out = analyze_weather_risks(weather)
    levels = [a["level"] for a in out]
    rank = {"critical": 0, "warning": 1, "info": 2}
    assert levels == sorted(levels, key=lambda lv: rank.get(lv, 9))
    # critical drought present
    assert any(a["kind"] == "drought" and a["level"] == "critical" for a in out)


def test_format_markdown_renders_advisories():
    advisories = analyze_weather_risks(
        {"daily_forecasts": [_daily(offset=0, tmin=-3, tmax=10)]},
        main_crop="사과",
    )
    md = format_advisories_markdown(advisories, main_crop="사과")
    assert "🔴" in md or "🟠" in md
    assert "사과" in md
    assert "서리" in md or "동해" in md


# ── Snow / 폭설 advisory ────────────────────────────────────────────────────


def test_snow_warning_at_5mm_with_snow_sky():
    weather = {
        "daily_forecasts": [
            _daily(offset=1, tmin=-2, tmax=1, precip=6.0, sky="눈"),
        ],
    }
    out = analyze_weather_risks(weather)
    snow = [a for a in out if a["kind"] == "snow"]
    assert snow, "snow advisory expected for 6mm precip + 눈 sky"
    assert snow[0]["level"] == "warning"
    assert snow[0]["value"] == 6.0


def test_snow_critical_at_10mm_with_snow_sky():
    weather = {
        "daily_forecasts": [
            _daily(offset=1, tmin=-3, tmax=0, precip=12.0, sky="눈"),
        ],
    }
    out = analyze_weather_risks(weather)
    snow = [a for a in out if a["kind"] == "snow"]
    assert snow and snow[0]["level"] == "critical"


def test_snow_below_threshold_emits_nothing():
    weather = {
        "daily_forecasts": [
            _daily(offset=1, tmin=-2, tmax=1, precip=3.0, sky="눈"),  # < 5mm
        ],
    }
    out = analyze_weather_risks(weather)
    assert not [a for a in out if a["kind"] == "snow"]


def test_snow_requires_snow_sky_keyword():
    # 강수 ≥ 5mm 이지만 sky 가 "비" 면 snow advisory 발생 금지 (heavy_rain
    # 임계 30mm 미만이라 heavy_rain 도 발생 안 함 — info rain_likely 만 가능).
    weather = {
        "daily_forecasts": [
            _daily(offset=1, tmin=2, tmax=6, precip=8.0, sky="비"),
        ],
    }
    out = analyze_weather_risks(weather)
    assert not [a for a in out if a["kind"] == "snow"]


def test_snow_matches_jin_nun_kkae_bi_sky():
    # "진눈깨비" 도 snow_sky 마커로 매칭되어야 한다.
    weather = {
        "daily_forecasts": [
            _daily(offset=0, tmin=-1, tmax=2, precip=7.0, sky="진눈깨비"),
        ],
    }
    out = analyze_weather_risks(weather)
    assert any(a["kind"] == "snow" for a in out)


def test_snow_matches_bi_nun_combined_sky():
    # KMA PTY 코드 2 → "비/눈" 라벨도 snow 로 인식 (substring "눈" 포함).
    weather = {
        "daily_forecasts": [
            _daily(offset=1, tmin=0, tmax=3, precip=11.0, sky="비/눈"),
        ],
    }
    out = analyze_weather_risks(weather)
    snow = [a for a in out if a["kind"] == "snow"]
    assert snow and snow[0]["level"] == "critical"


def test_snow_attaches_crop_hint_for_greenhouse_crop():
    weather = {
        "daily_forecasts": [
            _daily(offset=1, tmin=-2, tmax=1, precip=11.0, sky="눈"),
        ],
    }
    out = analyze_weather_risks(weather, main_crop="토마토")
    snow = next(a for a in out if a["kind"] == "snow")
    assert snow["crop_hint"] and "비닐하우스" in snow["crop_hint"]


def test_snow_unknown_crop_leaves_hint_none():
    weather = {
        "daily_forecasts": [
            _daily(offset=1, tmin=-2, tmax=1, precip=11.0, sky="눈"),
        ],
    }
    out = analyze_weather_risks(weather, main_crop="이름없는작물")
    snow = next(a for a in out if a["kind"] == "snow")
    assert snow["crop_hint"] is None


def test_snow_advisory_keeps_severity_sort_order():
    weather = {
        "current": {"wind_speed": 1.0, "temperature": -2, "precipitation_type": "없음"},
        "daily_forecasts": [
            _daily(offset=0, tmin=-2, tmax=1, precip=12.0, sky="눈"),  # critical snow
            _daily(offset=1, tmin=1.5, tmax=8, precip=0.0),             # warning frost
        ],
    }
    out = analyze_weather_risks(weather)
    levels = [a["level"] for a in out]
    rank = {"critical": 0, "warning": 1, "info": 2}
    assert levels == sorted(levels, key=lambda lv: rank.get(lv, 9))


def test_format_markdown_renders_snow_block():
    advisories = analyze_weather_risks(
        {"daily_forecasts": [_daily(offset=1, tmin=-2, tmax=1, precip=11, sky="눈")]},
        main_crop="토마토",
    )
    md = format_advisories_markdown(advisories, main_crop="토마토")
    assert "적설" in md
    assert "토마토" in md
    assert "🔴" in md  # critical


# ── Diurnal temperature swing (일교차) ──────────────────────────────────────


def test_temp_swing_warning_at_16c():
    weather = {
        "daily_forecasts": [
            _daily(offset=0, tmin=8, tmax=24),  # swing 16 → warning
        ],
    }
    out = analyze_weather_risks(weather)
    swing = [a for a in out if a["kind"] == "temp_swing"]
    assert swing and swing[0]["level"] == "warning"
    assert swing[0]["value"] == 16


def test_temp_swing_critical_at_20c():
    weather = {
        "daily_forecasts": [
            _daily(offset=1, tmin=5, tmax=25),  # swing 20 → critical
        ],
    }
    out = analyze_weather_risks(weather)
    swing = [a for a in out if a["kind"] == "temp_swing"]
    assert swing and swing[0]["level"] == "critical"


def test_temp_swing_below_threshold_emits_nothing():
    weather = {
        "daily_forecasts": [
            _daily(offset=0, tmin=10, tmax=25),  # swing 15 < 16 → no advisory
        ],
    }
    out = analyze_weather_risks(weather)
    assert not [a for a in out if a["kind"] == "temp_swing"]


def test_temp_swing_requires_both_tmin_and_tmax():
    # tmin or tmax 결측이면 advisory 발생 금지 (보수적).
    weather_a = {"daily_forecasts": [{"day_offset": 0, "temp_max": 30}]}
    weather_b = {"daily_forecasts": [{"day_offset": 0, "temp_min": 5}]}
    assert not [a for a in analyze_weather_risks(weather_a) if a["kind"] == "temp_swing"]
    assert not [a for a in analyze_weather_risks(weather_b) if a["kind"] == "temp_swing"]


def test_temp_swing_attaches_crop_hint_for_known_crop():
    weather = {"daily_forecasts": [_daily(offset=0, tmin=8, tmax=25)]}  # swing 17
    out = analyze_weather_risks(weather, main_crop="사과")
    swing = next(a for a in out if a["kind"] == "temp_swing")
    assert swing["crop_hint"] and "열과" in swing["crop_hint"]


def test_temp_swing_unknown_crop_leaves_hint_none():
    weather = {"daily_forecasts": [_daily(offset=0, tmin=8, tmax=25)]}
    out = analyze_weather_risks(weather, main_crop="이름없는작물")
    swing = next(a for a in out if a["kind"] == "temp_swing")
    assert swing["crop_hint"] is None


def test_temp_swing_independent_of_frost_and_heatwave():
    # 같은 날 frost(tmin≤2) + heatwave(tmax≥33) 가 동시 안 일어나지만,
    # 일교차는 다른 작물 영향이라 frost 와 동시 발화 가능해야 함.
    weather = {
        "daily_forecasts": [
            _daily(offset=0, tmin=-3, tmax=14),  # frost critical + swing 17 warning
        ],
    }
    out = analyze_weather_risks(weather)
    kinds = {(a["kind"], a["level"]) for a in out}
    assert ("frost", "critical") in kinds
    assert ("temp_swing", "warning") in kinds


def test_temp_swing_keeps_severity_sort_order():
    weather = {
        "daily_forecasts": [
            _daily(offset=0, tmin=5, tmax=26),  # swing 21 → critical
            _daily(offset=1, tmin=8, tmax=24),  # swing 16 → warning
        ],
    }
    out = analyze_weather_risks(weather)
    levels = [a["level"] for a in out if a["kind"] == "temp_swing"]
    assert levels == ["critical", "warning"]


# ── Cold wave (한파/혹한) advisory ────────────────────────────────────────────


def test_cold_wave_warning_at_minus_10c():
    weather = {
        "daily_forecasts": [
            _daily(offset=1, tmin=-10.0, tmax=-2),  # exactly at warning threshold
        ],
    }
    out = analyze_weather_risks(weather)
    cw = [a for a in out if a["kind"] == "cold_wave"]
    assert cw, "cold_wave advisory expected at tmin=-10"
    assert cw[0]["level"] == "warning"
    assert cw[0]["value"] == -10.0


def test_cold_wave_critical_at_minus_15c():
    weather = {
        "daily_forecasts": [
            _daily(offset=0, tmin=-16.0, tmax=-5),  # below critical threshold
        ],
    }
    out = analyze_weather_risks(weather)
    cw = [a for a in out if a["kind"] == "cold_wave"]
    assert cw and cw[0]["level"] == "critical"


def test_cold_wave_below_threshold_emits_nothing():
    weather = {
        "daily_forecasts": [
            _daily(offset=1, tmin=-8.0, tmax=2),  # > -10 → no cold_wave
        ],
    }
    out = analyze_weather_risks(weather)
    assert not [a for a in out if a["kind"] == "cold_wave"]


def test_cold_wave_fires_independently_with_frost():
    # tmin = -12℃ → frost critical AND cold_wave warning both fire (서로 다른 액션 셋).
    weather = {
        "daily_forecasts": [
            _daily(offset=0, tmin=-12.0, tmax=-3),
        ],
    }
    out = analyze_weather_risks(weather)
    kinds_levels = {(a["kind"], a["level"]) for a in out}
    assert ("frost", "critical") in kinds_levels  # ≤ -2 → critical frost
    assert ("cold_wave", "warning") in kinds_levels  # ≤ -10 but > -15 → warning cold_wave


def test_cold_wave_attaches_crop_hint_for_known_crop():
    weather = {
        "daily_forecasts": [_daily(offset=1, tmin=-11.0, tmax=-4)],
    }
    out = analyze_weather_risks(weather, main_crop="토마토")
    cw = next(a for a in out if a["kind"] == "cold_wave")
    assert cw["crop_hint"] and "난방" in cw["crop_hint"]


def test_cold_wave_unknown_crop_leaves_hint_none():
    weather = {
        "daily_forecasts": [_daily(offset=1, tmin=-11.0, tmax=-4)],
    }
    out = analyze_weather_risks(weather, main_crop="이름없는작물")
    cw = next(a for a in out if a["kind"] == "cold_wave")
    assert cw["crop_hint"] is None


def test_cold_wave_message_distinguishes_from_frost_advice():
    # 한파 메시지는 frost 의 "보온·살수" 가 아닌 "시설 난방·동파 방지" 액션을 가져야 한다
    # — 이게 본 카테고리 분리의 본질적 가치.
    weather = {
        "daily_forecasts": [_daily(offset=0, tmin=-13.0, tmax=-3)],
    }
    out = analyze_weather_risks(weather)
    cw = next(a for a in out if a["kind"] == "cold_wave")
    assert "난방" in cw["message"] or "동파" in cw["message"]
    assert "살수" not in cw["message"]


def test_cold_wave_keeps_severity_sort_order():
    weather = {
        "current": {"wind_speed": 1.0, "temperature": -10, "precipitation_type": "없음"},
        "daily_forecasts": [
            _daily(offset=0, tmin=-16, tmax=-5),  # critical cold_wave + critical frost
            _daily(offset=1, tmin=-10, tmax=-2),  # warning cold_wave + critical frost (≤-2)
        ],
    }
    out = analyze_weather_risks(weather)
    levels = [a["level"] for a in out]
    rank = {"critical": 0, "warning": 1, "info": 2}
    assert levels == sorted(levels, key=lambda lv: rank.get(lv, 9))


def test_cold_wave_missing_tmin_emits_nothing():
    weather = {"daily_forecasts": [{"day_offset": 0, "temp_max": -5}]}
    out = analyze_weather_risks(weather)
    assert not [a for a in out if a["kind"] == "cold_wave"]


# ── Wind chill (체감온도) advisory ──────────────────────────────────────────
# Environment Canada 공식 (단위 검증):
#   T = -10℃, V = 5 m/s = 18 km/h → V^0.16 ≈ 1.588
#     WC = 13.12 + 0.6215·(-10) - 11.37·1.588 + 0.3965·(-10)·1.588 ≈ -17.4℃
#   T = -15℃, V = 10 m/s = 36 km/h → V^0.16 ≈ 1.774
#     WC ≈ 13.12 - 9.32 - 20.17 - 10.55 ≈ -26.9℃ (critical)


def test_wind_chill_warning_at_minus_10c_with_moderate_wind():
    weather = {
        "daily_forecasts": [
            _daily(offset=0, tmin=-10.0, tmax=-2, wmax=5.0),  # WC ≈ -17 → warning
        ],
    }
    out = analyze_weather_risks(weather)
    wc = [a for a in out if a["kind"] == "wind_chill"]
    assert wc, "wind_chill advisory expected at tmin=-10, wmax=5m/s"
    assert wc[0]["level"] == "warning"
    assert -20 <= wc[0]["value"] <= -10


def test_wind_chill_critical_at_low_temp_high_wind():
    weather = {
        "daily_forecasts": [
            _daily(offset=1, tmin=-15.0, tmax=-5, wmax=10.0),  # WC ≈ -27 → critical
        ],
    }
    out = analyze_weather_risks(weather)
    wc = [a for a in out if a["kind"] == "wind_chill"]
    assert wc and wc[0]["level"] == "critical"
    assert wc[0]["value"] <= -20


def test_wind_chill_no_advisory_at_warm_temp_with_strong_wind():
    # T = 8℃ + V = 10 m/s → WC ≈ +3℃ (체감 양수) → 동상 위험 없음.
    weather = {
        "daily_forecasts": [
            _daily(offset=0, tmin=8.0, tmax=12, wmax=10.0),
        ],
    }
    out = analyze_weather_risks(weather)
    assert not [a for a in out if a["kind"] == "wind_chill"]


def test_wind_chill_no_advisory_at_calm_cold_temp():
    # 풍속 < 1.34 m/s 면 공식 유효구간 밖 → advisory 발생 안 함.
    weather = {
        "daily_forecasts": [
            _daily(offset=0, tmin=-12.0, tmax=-3, wmax=0.5),
        ],
    }
    out = analyze_weather_risks(weather)
    assert not [a for a in out if a["kind"] == "wind_chill"]


def test_wind_chill_no_advisory_above_max_temp():
    # T > 10℃ 도 공식 유효구간 밖.
    weather = {
        "daily_forecasts": [
            _daily(offset=0, tmin=12.0, tmax=18, wmax=12.0),
        ],
    }
    out = analyze_weather_risks(weather)
    assert not [a for a in out if a["kind"] == "wind_chill"]


def test_wind_chill_requires_both_tmin_and_wmax():
    weather_a = {"daily_forecasts": [{"day_offset": 0, "temp_max": -2, "wind_speed_max": 5.0}]}
    weather_b = {"daily_forecasts": [{"day_offset": 0, "temp_min": -10, "temp_max": -2}]}
    assert not [a for a in analyze_weather_risks(weather_a) if a["kind"] == "wind_chill"]
    assert not [a for a in analyze_weather_risks(weather_b) if a["kind"] == "wind_chill"]


def test_wind_chill_attaches_crop_hint_for_outdoor_crop():
    weather = {
        "daily_forecasts": [_daily(offset=0, tmin=-12.0, tmax=-3, wmax=6.0)],
    }
    out = analyze_weather_risks(weather, main_crop="사과")
    wc = next(a for a in out if a["kind"] == "wind_chill")
    assert wc["crop_hint"] and ("전정" in wc["crop_hint"] or "보온" in wc["crop_hint"])


def test_wind_chill_indoor_only_crop_leaves_hint_none():
    # 토마토 작물은 wind_chill crop_hint 미정의 (시설 내부 작업) → None.
    weather = {
        "daily_forecasts": [_daily(offset=0, tmin=-12.0, tmax=-3, wmax=6.0)],
    }
    out = analyze_weather_risks(weather, main_crop="토마토")
    wc = next(a for a in out if a["kind"] == "wind_chill")
    assert wc["crop_hint"] is None


def test_wind_chill_unknown_crop_leaves_hint_none():
    weather = {
        "daily_forecasts": [_daily(offset=0, tmin=-12.0, tmax=-3, wmax=6.0)],
    }
    out = analyze_weather_risks(weather, main_crop="이름없는작물")
    wc = next(a for a in out if a["kind"] == "wind_chill")
    assert wc["crop_hint"] is None


def test_wind_chill_fires_independently_with_frost_and_cold_wave():
    # tmin=-15, wmax=8 → frost critical + cold_wave critical + wind_chill critical
    # 세 advisory 가 동시 발화해야 한다 (서로 다른 액션 셋).
    weather = {
        "daily_forecasts": [_daily(offset=0, tmin=-15.0, tmax=-5, wmax=8.0)],
    }
    out = analyze_weather_risks(weather)
    kinds_levels = {(a["kind"], a["level"]) for a in out}
    assert ("frost", "critical") in kinds_levels
    assert ("cold_wave", "critical") in kinds_levels
    assert ("wind_chill", "critical") in kinds_levels


def test_wind_chill_keeps_severity_sort_order():
    weather = {
        "current": {"wind_speed": 1.0, "temperature": -5, "precipitation_type": "없음"},
        "daily_forecasts": [
            _daily(offset=0, tmin=-15, tmax=-5, wmax=10.0),  # critical wind_chill
            _daily(offset=1, tmin=-10, tmax=-2, wmax=5.0),   # warning wind_chill
        ],
    }
    out = analyze_weather_risks(weather)
    wc_levels = [a["level"] for a in out if a["kind"] == "wind_chill"]
    assert wc_levels == ["critical", "warning"]


def _run_all_tests() -> None:
    """Execute every test_* in this module without pytest."""
    import inspect
    import sys

    failed = 0
    for name, fn in sorted(inspect.getmembers(sys.modules[__name__], inspect.isfunction)):
        if not name.startswith("test_"):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as exc:  # noqa: PERF203
            failed += 1
            print(f"FAIL {name}: {exc}")
    if failed:
        raise SystemExit(f"{failed} failure(s)")
    print("All weather_alerts tests passed.")


if __name__ == "__main__":
    _run_all_tests()
