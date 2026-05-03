"""Tests for fast_path frost-shield pattern + formatter.

Pure unit tests: only the regex and the markdown formatter are exercised here
(the dispatcher itself is integration-tested elsewhere; here we keep DB &
weather IO out so the suite stays hermetic).
"""

from __future__ import annotations

from app.services.farm_agent.fast_path import (
    _BLOCKLIST,
    _FROST_RE,
    _format_frost,
)


# ── _FROST_RE matches & rejects ─────────────────────────────────────────────


def test_frost_re_matches_typical_korean_queries():
    samples = [
        "오늘 밤 서리?",
        "내일 새벽 영하?",
        "서리 위험?",
        "오늘 동해 가능?",
        "내일 결빙?",
        "이번 주 서리?",
        "이번주 영하 내려가?",
        "모레 새벽 서리 올까",
    ]
    for q in samples:
        assert _FROST_RE.match(q), f"expected match for: {q!r}"


def test_frost_re_rejects_unrelated_queries():
    samples = [
        "오늘 비 와?",                  # 비 → not frost
        "내일 영농일지 좀 보여줘",       # 영농 (not 영하)
        "이번 주 시세 어때?",
        "어제 환기 켰어?",
        "농약 희석 비율",                # blocklist territory
        "내일 강풍?",                    # strong wind, not frost
    ]
    for q in samples:
        assert not _FROST_RE.match(q), f"expected NO match for: {q!r}"


def test_blocklist_does_not_intersect_frost_keywords():
    # Sanity: blocklist must not capture frost terms.
    for kw in ("서리", "동해", "영하", "결빙"):
        assert not _BLOCKLIST.search(kw), (
            f"blocklist accidentally blocks frost keyword: {kw}"
        )


# ── _format_frost rendering ─────────────────────────────────────────────────


def _frost_advisory(level: str = "warning", when: str = "내일") -> dict:
    return {
        "level": level,
        "kind": "frost",
        "when": when,
        "message": f"{when} 최저 0.5℃ — 서리/동해 가능, 보온·살수 검토.",
        "value": 0.5,
        "crop_hint": None,
    }


def test_format_frost_renders_warning_with_icon():
    out = _format_frost([_frost_advisory()], None, {"daily_forecasts": []})
    assert "🧊 서리/동해 위험" in out
    assert "🟠" in out
    assert "서리" in out


def test_format_frost_promotes_critical_icon():
    adv = _frost_advisory(level="critical")
    out = _format_frost([adv], "사과", {"daily_forecasts": []})
    assert "🔴" in out
    assert "사과" in out


def test_format_frost_includes_crop_hint_when_present():
    adv = _frost_advisory()
    adv["crop_hint"] = "개화기 서리 시 결실률 급락. 살수·송풍기 가동 검토."
    out = _format_frost([adv], "사과", {"daily_forecasts": []})
    assert "개화기" in out


def test_format_frost_empty_shows_safe_no_risk_block_with_tmin_refs():
    weather = {
        "daily_forecasts": [
            {"day_offset": 0, "temp_min": 8, "temp_max": 18},
            {"day_offset": 1, "temp_min": 5, "temp_max": 16},
        ]
    }
    out = _format_frost([], None, weather)
    assert "위험 없음" in out
    # Both forecast tmins should be referenced so user can verify the call.
    assert "오늘 8" in out
    assert "내일 5" in out


def test_format_frost_empty_with_no_forecasts_falls_back():
    out = _format_frost([], None, {"daily_forecasts": []})
    assert "위험 없음" in out
    assert "예보 미확정" in out


def test_format_frost_filters_non_frost_advisories():
    # If caller passes a mixed list (e.g., wind + frost), only frost should render.
    adv_list = [
        {"kind": "strong_wind", "level": "warning", "when": "내일", "message": "...",
         "value": 10.0, "crop_hint": None},
        _frost_advisory(),
    ]
    out = _format_frost(adv_list, None, {"daily_forecasts": []})
    assert "서리" in out
    assert "강풍" not in out  # not in any frost message
    assert "풍속" not in out


def _run_all_tests() -> None:
    """Run all test_* without pytest (mirrors test_weather_alerts.py pattern)."""
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
    print("All fast_path tests passed.")


if __name__ == "__main__":
    _run_all_tests()
