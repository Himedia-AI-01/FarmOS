"""Product image specs for shoppingmall seed.

Provides PRODUCT_IMAGE_SPECS (id-keyed dataclass with .name) and
_build_images(pid) -> (thumbnail_url, [image_urls]) consumed by
bootstrap/shoppingmall_seed.py. Uses picsum.photos placeholders.
Replace with real CDN URLs when product photography is available.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _Spec:
    name: str


# Must stay in lockstep with PRODUCT_NAMES in bootstrap/shoppingmall_seed.py.
PRODUCT_NAMES = [
    "경북 부사 사과 5kg", "충남 신고배 7.5kg", "청송 꿀사과 3kg", "나주배 선물세트 5kg",
    "홍로사과 2kg", "제주 감귤 5kg", "제주 한라봉 3kg", "카라카라 오렌지 2kg",
    "천혜향 2kg", "레드향 3kg", "유기농 상추 300g", "깻잎 100매", "시금치 500g",
    "배추 1포기", "청경채 200g", "감자 3kg", "고구마 3kg", "당근 1kg", "양파 3kg",
    "무 1개", "한우 등심 1++ 300g", "한우 갈비살 500g", "한우 채끝 200g",
    "한우 불고기용 300g", "한우 사골 2kg", "제주 흑돼지 삼겹살 500g", "목살 구이용 500g",
    "돼지갈비 양념 1kg", "노르웨이 생연어 300g", "제주 광어회 500g", "고등어 2마리",
    "참치회 400g", "갈치 2마리", "통영 생굴 1kg", "킹크랩 1마리 (1.5kg)",
    "새우 (대) 1kg", "전복 10마리", "오징어 3마리", "유기농 블루베리 500g",
    "친환경 방울토마토 1kg", "유기농 브로콜리 2개", "흙당근 2kg",
]

PRODUCT_IMAGE_SPECS = {i + 1: _Spec(name=n) for i, n in enumerate(PRODUCT_NAMES)}


def _build_images(pid: int) -> tuple[str, list[str]]:
    thumb = f"https://picsum.photos/seed/p{pid}/300/300"
    imgs = [f"https://picsum.photos/seed/p{pid}-{j}/600/600" for j in range(3)]
    return thumb, imgs
