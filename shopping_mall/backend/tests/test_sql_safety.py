from app.core.sql_safety import contains_like_pattern, escape_like_pattern, normalize_search_term


def test_normalize_search_term_collapses_whitespace_and_limits_length():
    assert normalize_search_term("  딸기   토마토  ") == "딸기 토마토"
    assert normalize_search_term("가" * 100, max_length=10) == "가" * 10


def test_escape_like_pattern_treats_wildcards_as_literals():
    assert escape_like_pattern(r"100%_fresh\fruit") == r"100\%\_fresh\\fruit"
    assert contains_like_pattern("딸기%") == r"%딸기\%%"
