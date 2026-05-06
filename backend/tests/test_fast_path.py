"""Tests for fast_path frost-shield pattern + formatter.

Pure unit tests: only the regex and the markdown formatter are exercised here
(the dispatcher itself is integration-tested elsewhere; here we keep DB &
weather IO out so the suite stays hermetic).
"""

from __future__ import annotations

from app.services.farm_agent.fast_path import (
    _BLOCKLIST,
    _FROST_RE,
    _GENERAL_RISK_RE,
    _format_frost,
    _format_general_risks,
    _select_risk_kinds,
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


# ── _GENERAL_RISK_RE matches & rejects ─────────────────────────────────────


def test_general_risk_re_matches_typical_queries():
    samples = [
        "이번주 폭염?",
        "내일 폭우 와?",
        "오늘 호우?",
        "이번주 장마",
        "오늘 강풍?",
        "내일 돌풍?",
        "오늘 특보?",
        "내일 주의보?",
        "이번주 한파",
        "오늘 혹한?",
        "이번주 혹서?",
    ]
    for q in samples:
        assert _GENERAL_RISK_RE.match(q), f"expected match for: {q!r}"


def test_general_risk_re_rejects_unrelated_queries():
    samples = [
        "오늘 비 와?",          # 일반 비 → _WEATHER_RE 영역
        "오늘 날씨 어때?",       # 일반 날씨
        "내일 서리?",            # frost — 별도 _FROST_RE
        "어제 환기 켰어?",       # IoT
        "농약 폭염 살포",        # blocklist (농약) 가 fast-path 자체 차단
        "이번 주 시세 어때?",
    ]
    for q in samples:
        # blocklist 류는 dispatcher 에서 막히므로 regex 자체 매칭은 허용될 수
        # 있다. 따라서 여기서는 "농약 폭염 살포" 는 검사 제외.
        if "농약" in q:
            assert _BLOCKLIST.search(q)
            continue
        assert not _GENERAL_RISK_RE.match(q), f"expected NO match for: {q!r}"


def test_general_risk_re_does_not_collide_with_frost_keywords():
    for kw in ("서리", "동해", "영하", "결빙"):
        assert not _GENERAL_RISK_RE.match(kw), (
            f"general-risk regex unexpectedly catches frost keyword: {kw}"
        )


# ── _select_risk_kinds intent classifier ───────────────────────────────────


def test_select_risk_kinds_heatwave():
    kinds, title = _select_risk_kinds("이번주 폭염?")
    assert kinds == frozenset({"heatwave"})
    assert "폭염" in title


def test_select_risk_kinds_rain_family():
    for q in ("내일 폭우?", "오늘 호우?", "이번주 장마"):
        kinds, _ = _select_risk_kinds(q)
        assert "heavy_rain" in (kinds or set())
        assert "rain_likely" in (kinds or set())


def test_select_risk_kinds_wind_family():
    kinds, _ = _select_risk_kinds("오늘 강풍?")
    assert kinds == frozenset({"strong_wind"})


def test_select_risk_kinds_cold_wave_maps_to_frost_and_cold_wave():
    # 한파 질의는 frost (≤2℃ 서리) 와 cold_wave (≤-10℃ 한파) 두 카테고리를 함께 노출.
    # 두 kind 의 권장 액션이 다르므로 (살수 vs 시설 난방·동파 방지) 분리해 보여준다.
    kinds, title = _select_risk_kinds("이번주 한파?")
    assert kinds == frozenset({"frost", "cold_wave"})
    assert "한파" in title or "저온" in title


def test_select_risk_kinds_hokhan_also_maps_to_cold_wave():
    # "혹한" 키워드도 동일하게 frost+cold_wave 두 카테고리에 매핑.
    kinds, _ = _select_risk_kinds("내일 혹한?")
    assert kinds == frozenset({"frost", "cold_wave"})


def test_select_risk_kinds_fallback_when_no_specific_keyword():
    kinds, title = _select_risk_kinds("내일 특보?")
    assert kinds is None
    assert "기상" in title


# ── _format_general_risks rendering & filtering ────────────────────────────


def _adv(kind: str, level: str = "warning", when: str = "내일",
         msg: str = "...", crop_hint: str | None = None) -> dict:
    return {
        "level": level, "kind": kind, "when": when, "message": msg,
        "value": 0, "crop_hint": crop_hint,
    }


def test_format_general_risks_filters_to_requested_kind():
    advisories = [
        _adv("heatwave", "critical", "내일", "내일 최고 36℃ — 고온 스트레스."),
        _adv("strong_wind", "warning", "내일", "내일 풍속 10m/s — ..."),
        _adv("frost", "warning", "내일", "내일 최저 1℃ — 서리/동해 가능."),
    ]
    out = _format_general_risks("이번주 폭염?", advisories, None)
    assert "🔥" in out
    assert "고온" in out
    # 다른 kind 의 메시지/단어는 빠져야 한다.
    assert "풍속" not in out
    assert "서리" not in out


def test_format_general_risks_rain_family_includes_rain_likely():
    advisories = [
        _adv("rain_likely", "info", "내일", "내일 강수확률 80%"),
        _adv("heavy_rain", "critical", "내일", "내일 강수 90mm — 호우 특보 수준."),
    ]
    out = _format_general_risks("내일 폭우?", advisories, None)
    assert "강수확률 80%" in out
    assert "90mm" in out


def test_format_general_risks_empty_returns_no_risk_block():
    out = _format_general_risks("이번주 폭염?", [], "사과")
    assert "특이사항 없음" in out or "위험 없음" in out
    # 작물 라벨이 헤더에 붙어야 한다.
    assert "사과" in out


def test_format_general_risks_critical_promotes_red_icon():
    advisories = [_adv("heatwave", level="critical", msg="내일 최고 36℃ — 고온 스트레스.")]
    out = _format_general_risks("폭염?", advisories, None)
    assert "🔴" in out


def test_format_general_risks_includes_crop_hint_when_present():
    advisories = [
        _adv(
            "heatwave",
            level="warning",
            msg="내일 최고 34℃ — 고온 스트레스.",
            crop_hint="송이 일소피해, 당도 저하 우려.",
        )
    ]
    out = _format_general_risks("폭염?", advisories, "포도")
    assert "송이 일소피해" in out


def test_format_general_risks_fallback_keeps_only_critical_and_warning():
    # 특보/주의보 류 fallback 경로 — info advisory 는 잡음으로 간주해 숨긴다.
    advisories = [
        _adv("heatwave", "critical", "오늘", "오늘 최고 36℃."),
        _adv("rain_likely", "info", "내일", "내일 강수확률 70%."),
        _adv("strong_wind", "warning", "오늘", "오늘 풍속 10m/s."),
    ]
    out = _format_general_risks("오늘 특보?", advisories, None)
    assert "오늘 최고 36℃" in out
    assert "오늘 풍속 10m/s" in out
    assert "강수확률 70%" not in out


# ── Snow keyword routing ──────────────────────────────────────────────────


def test_general_risk_re_matches_snow_keywords():
    samples = [
        "내일 폭설?",
        "이번주 대설",
        "오늘 적설",
        "내일 폭설 와?",
    ]
    for q in samples:
        assert _GENERAL_RISK_RE.match(q), f"expected match for: {q!r}"


def test_general_risk_re_rejects_bare_nun():
    # 단독 "눈" 은 의미 충돌(눈높이/눈매 등) — fast-path 매칭 금지.
    samples = ["오늘 눈?", "내일 눈 와?", "눈"]
    for q in samples:
        assert not _GENERAL_RISK_RE.match(q), f"expected NO match for: {q!r}"


def test_select_risk_kinds_snow_family():
    for q in ("내일 폭설?", "이번주 대설", "오늘 적설"):
        kinds, title = _select_risk_kinds(q)
        assert kinds == frozenset({"snow"}), f"unexpected kinds for {q!r}: {kinds}"
        assert "폭설" in title or "적설" in title


def test_format_general_risks_snow_filters_to_snow_only():
    advisories = [
        _adv("snow", "critical", "내일", "내일 적설 약 12cm 예상 — 시설 적설하중 위험."),
        _adv("strong_wind", "warning", "내일", "내일 풍속 10m/s — 방제 보류."),
        _adv("frost", "warning", "내일", "내일 최저 -2℃ — 서리 가능."),
    ]
    out = _format_general_risks("내일 폭설?", advisories, None)
    assert "❄️" in out
    assert "12cm" in out
    assert "풍속" not in out
    assert "서리" not in out


def test_format_general_risks_snow_empty_returns_no_risk_block():
    out = _format_general_risks("내일 폭설?", [], "토마토")
    assert "특이사항 없음" in out or "위험 없음" in out
    assert "토마토" in out


# ── Diurnal swing (일교차) keyword routing ────────────────────────────────


def test_general_risk_re_matches_diurnal_keywords():
    samples = [
        "오늘 일교차?",
        "내일 일교차 어때",
        "이번주 일교차",
        "모레 일교차 클까",
        "일교차?",
    ]
    for q in samples:
        assert _GENERAL_RISK_RE.match(q), f"expected match for: {q!r}"


def test_general_risk_re_rejects_lookalike_diurnal_terms():
    # "기온차" 는 _WEATHER_RE 의 "기온" prefix 와 충돌하므로 의도적으로
    # _GENERAL_RISK_RE 에서 매칭 금지(일반 날씨 흐름으로 위임).
    # "온도차" 는 실내/구역간 의미로도 쓰여 모호 — fast-path 자체 거부.
    samples = ["오늘 기온차?", "내일 온도차?"]
    for q in samples:
        assert not _GENERAL_RISK_RE.match(q), f"expected NO match for: {q!r}"


def test_select_risk_kinds_diurnal_family():
    for q in ("오늘 일교차?", "내일 일교차 클까", "이번주 일교차"):
        kinds, title = _select_risk_kinds(q)
        assert kinds == frozenset({"temp_swing"}), (
            f"unexpected kinds for {q!r}: {kinds}"
        )
        assert "일교차" in title


def test_format_general_risks_diurnal_filters_to_temp_swing_only():
    advisories = [
        _adv("temp_swing", "warning", "내일",
             "내일 일교차 17℃ (5~22℃) — 전이기 큰 기온차 주의."),
        _adv("frost", "warning", "내일", "내일 최저 1℃ — 서리 가능."),
        _adv("heatwave", "warning", "내일", "내일 최고 33℃ — 고온."),
    ]
    out = _format_general_risks("내일 일교차?", advisories, None)
    assert "🌡️" in out
    assert "일교차 17" in out
    # 다른 kind 의 메시지는 빠져야 한다.
    assert "서리" not in out
    assert "고온" not in out


def test_format_general_risks_diurnal_critical_promotes_red_icon():
    advisories = [
        _adv("temp_swing", "critical", "내일",
             "내일 일교차 21℃ (3~24℃) — 열과·갈라짐 빈발 임계."),
    ]
    out = _format_general_risks("내일 일교차?", advisories, "사과")
    assert "🔴" in out
    assert "사과" in out


def test_format_general_risks_diurnal_empty_returns_no_risk_block():
    out = _format_general_risks("이번주 일교차?", [], "토마토")
    assert "특이사항 없음" in out or "위험 없음" in out
    assert "토마토" in out


# ── Tropical night (열대야) keyword routing ───────────────────────────────


def test_general_risk_re_matches_tropical_night_keywords():
    samples = [
        "오늘 열대야?",
        "내일 열대야 와?",
        "이번주 열대야",
        "오늘 초열대야?",
        "내일 초열대야 와",
        "열대야?",
    ]
    for q in samples:
        assert _GENERAL_RISK_RE.match(q), f"expected match for: {q!r}"


def test_general_risk_re_rejects_lookalike_tropical_night_terms():
    # 단독 "야간" 은 야외/실내·작업·온도 등 의미 모호 — fast-path 자체 거부.
    # "한밤" 은 시점 한정사일 뿐 위험 카테고리가 아니므로 거부.
    samples = ["오늘 야간?", "내일 한밤 더워?", "오늘 밤 더워?"]
    for q in samples:
        assert not _GENERAL_RISK_RE.match(q), f"expected NO match for: {q!r}"


def test_select_risk_kinds_tropical_night_family():
    for q in ("오늘 열대야?", "내일 열대야 와?", "이번주 초열대야"):
        kinds, title = _select_risk_kinds(q)
        assert kinds == frozenset({"tropical_night"}), (
            f"unexpected kinds for {q!r}: {kinds}"
        )
        assert "열대야" in title


def test_format_general_risks_tropical_night_filters_to_tropical_night_only():
    advisories = [
        _adv("tropical_night", "warning", "내일",
             "내일 야간 최저 26℃ — 열대야: 야간 환기·미스트 사이클 검토."),
        _adv("heatwave", "warning", "내일", "내일 최고 33℃ — 고온."),
        _adv("strong_wind", "warning", "내일", "내일 풍속 10m/s — 방제 보류."),
    ]
    out = _format_general_risks("내일 열대야?", advisories, None)
    assert "🌙" in out
    assert "야간 최저 26" in out
    # 다른 kind 의 메시지는 빠져야 한다 — 질의 의도가 야간 한정.
    assert "최고 33" not in out
    assert "풍속" not in out


def test_format_general_risks_tropical_night_critical_promotes_red_icon():
    advisories = [
        _adv("tropical_night", "critical", "내일",
             "내일 야간 최저 28℃ — 초열대야 임계: 화분 활력 저하·낙화 주의."),
    ]
    out = _format_general_risks("내일 초열대야?", advisories, "토마토")
    assert "🔴" in out
    assert "토마토" in out


def test_format_general_risks_tropical_night_empty_returns_no_risk_block():
    out = _format_general_risks("오늘 열대야?", [], "벼")
    assert "특이사항 없음" in out or "위험 없음" in out
    assert "벼" in out


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
