"""KAMIS 품목명 ↔ FarmOS 도메인 용어 alias 테이블.

문제:
  KAMIS 는 유통/소매 단계 품목명을 사용한다 — "쌀", "찐쌀", "현미", ...
  FarmOS 는 재배 단계 품목명을 사용한다 — "벼" (논 작물), "콩"(밭 작물).
  사용자가 프로필에 main_crop="벼"를 등록하면 KAMIS 응답에서 "쌀" 행을
  매칭해야 한다. 단순 substring 매칭은 실패한다.

설계:
  - 단일 dict CROP_ALIAS_MAP: FarmOS canonical term → KAMIS 후보 키워드 list.
    한 농산물이 여러 KAMIS 품목으로 나뉠 수 있으므로 list (예: 벼 → [쌀, 찐쌀, 현미]).
  - resolve_kamis_terms(user_term): 입력어를 KAMIS 검색 키워드 list 로 변환.
    매핑 미정의면 원래 단어를 그대로 반환 (graceful — 매핑 없을 때 동작 유지).
  - matches_kamis_item(user_term, kamis_item_name): substring 비교 헬퍼.

확장:
  새 작물 추가는 본 dict 한 곳에서만. 정확한 KAMIS 품목명은
  https://www.kamis.or.kr/customer/price/retail/catalogue.do 에서 확인.
  잘못 매핑하면 무관한 시세가 반환되므로 추가 전 KAMIS 카탈로그 확인 필수.
"""

from __future__ import annotations

# FarmOS canonical (재배) → KAMIS (유통) 키워드 후보.
# 후보 list 의 어느 하나라도 KAMIS item_name 에 substring 포함되면 매칭.
CROP_ALIAS_MAP: dict[str, list[str]] = {
    # 곡물: 재배명 ≠ 유통명
    "벼": ["쌀", "찐쌀", "현미"],
    "콩": ["콩", "백태", "서리태"],
    "팥": ["팥"],
    "보리": ["보리쌀", "겉보리"],

    # 채소: 대부분 동일하지만 가공형이 별도로 존재
    "고추": ["고추", "건고추", "홍고추", "청양고추"],
    "마늘": ["마늘", "깐마늘", "다진마늘"],
    "양파": ["양파"],
    "배추": ["배추", "절임배추"],
    "무": ["무"],
    "감자": ["감자"],
    "고구마": ["고구마"],

    # 과일: 일반적으로 1:1
    "사과": ["사과"],
    "배": ["배"],
    "딸기": ["딸기"],
    "토마토": ["토마토", "방울토마토"],
    "포도": ["포도", "샤인머스캣"],

    # TODO(user): FarmOS 가 지원하는 작물 카탈로그를 따라 확장.
    # 추가 시 KAMIS 카탈로그(품목명 정확)를 먼저 확인할 것.
}


def resolve_kamis_terms(user_term: str) -> list[str]:
    """입력어를 KAMIS 검색용 키워드 list 로 변환.

    Args:
        user_term: 사용자/프로필이 사용하는 한국어 작물명 (예: "벼").

    Returns:
        KAMIS item_name 검색용 키워드 list. 매핑이 없으면 [user_term] 반환
        (직접 매칭 시도 — 채소·과일 대부분은 동일하므로 안전한 default).
        빈 입력은 [].
    """
    term = (user_term or "").strip()
    if not term:
        return []
    return CROP_ALIAS_MAP.get(term, [term])


def matches_kamis_item(user_term: str, kamis_item_name: str | None) -> bool:
    """KAMIS 응답 1행이 사용자 작물에 해당하는지 판정.

    Substring 매칭 — KAMIS 는 등급/규격을 품목명에 붙여 보내므로 정확 일치는 너무 엄격.
    예: "사과" → KAMIS 의 "사과(후지)" 매칭. 거짓 양성 우려가 있는 짧은 단어는
    CROP_ALIAS_MAP 후보를 더 구체적으로 두어 방어 (예: "감" 단독 매핑은 두지 않음).
    """
    haystack = kamis_item_name or ""
    return any(term in haystack for term in resolve_kamis_terms(user_term))
