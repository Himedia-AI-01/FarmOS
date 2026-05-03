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
