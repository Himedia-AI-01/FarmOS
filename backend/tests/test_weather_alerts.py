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


def test_format_markdown_renders_advisories():
    advisories = analyze_weather_risks(
        {"daily_forecasts": [_daily(offset=0, tmin=-3, tmax=10)]},
        main_crop="사과",
    )
    md = format_advisories_markdown(advisories, main_crop="사과")
    assert "🔴" in md or "🟠" in md
    assert "사과" in md
    assert "서리" in md or "동해" in md


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
