"""SQL query safety helpers.

사용자 입력은 SQLAlchemy가 바인딩하지만, LIKE 검색의 와일드카드 문자까지
검색 패턴으로 해석되면 의도보다 넓은 결과가 반환될 수 있다.
"""

LIKE_ESCAPE_CHAR = "\\"
_LIKE_WILDCARDS = ("\\", "%", "_")


def normalize_search_term(value: str, *, max_length: int = 80) -> str:
    """검색어의 공백과 길이를 정규화한다."""
    return " ".join((value or "").strip().split())[:max_length]


def escape_like_pattern(value: str) -> str:
    """LIKE/ILIKE 패턴에서 사용자 입력을 리터럴 문자열로 취급하게 이스케이프한다."""
    escaped = value
    for char in _LIKE_WILDCARDS:
        escaped = escaped.replace(char, LIKE_ESCAPE_CHAR + char)
    return escaped


def contains_like_pattern(value: str) -> str:
    """부분 일치 검색용 안전 LIKE 패턴을 만든다."""
    return f"%{escape_like_pattern(value)}%"
