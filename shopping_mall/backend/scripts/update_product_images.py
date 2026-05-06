"""Update demo ShoppingMall image URLs with deterministic remote images.

This script is safe to run repeatedly. It updates only image URL fields used by
demo data: Product.thumbnail, Product.images, Store.image_url, and Review.images.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import SessionLocal
from app.models.product import Product
from app.models.review import Review
from app.models.store import Store


@dataclass(frozen=True)
class ProductImageSpec:
    name: str
    keywords: str
    lock: int


PRODUCT_IMAGE_SPECS: dict[int, ProductImageSpec] = {
    1: ProductImageSpec("경북 부사 사과 5kg", "red-apple,fruit", 101),
    2: ProductImageSpec("충남 신고배 7.5kg", "asian-pear,fruit", 102),
    3: ProductImageSpec("청송 꿀사과 3kg", "apple,orchard", 103),
    4: ProductImageSpec("나주배 선물세트 5kg", "pear,fruit", 104),
    5: ProductImageSpec("홍로사과 2kg", "red-apple,orchard", 105),
    6: ProductImageSpec("제주 감귤 5kg", "tangerine,citrus", 106),
    7: ProductImageSpec("제주 한라봉 3kg", "orange,citrus", 107),
    8: ProductImageSpec("카라카라 오렌지 2kg", "orange,fruit", 108),
    9: ProductImageSpec("천혜향 2kg", "mandarin,citrus", 109),
    10: ProductImageSpec("레드향 3kg", "orange,citrus-fruit", 110),
    11: ProductImageSpec("유기농 상추 300g", "lettuce,vegetable", 111),
    12: ProductImageSpec("깻잎 100매", "perilla,leaf,vegetable", 112),
    13: ProductImageSpec("시금치 500g", "spinach,vegetable", 113),
    14: ProductImageSpec("배추 1포기", "napa-cabbage,vegetable", 114),
    15: ProductImageSpec("청경채 200g", "bok-choy,vegetable", 115),
    16: ProductImageSpec("감자 3kg", "potato,vegetable", 116),
    17: ProductImageSpec("고구마 3kg", "sweet-potato,vegetable", 117),
    18: ProductImageSpec("당근 1kg", "carrot,vegetable", 118),
    19: ProductImageSpec("양파 3kg", "onion,vegetable", 119),
    20: ProductImageSpec("무 1개", "radish,vegetable", 120),
    21: ProductImageSpec("한우 등심 1++ 300g", "steak,beef", 1),
    22: ProductImageSpec("한우 갈비살 500g", "beef-ribs,meat", 122),
    23: ProductImageSpec("한우 채끝 200g", "beef-steak,raw-meat", 123),
    24: ProductImageSpec("한우 불고기용 300g", "sliced-beef,meat", 124),
    25: ProductImageSpec("한우 사골 2kg", "beef-bone,soup", 125),
    26: ProductImageSpec("제주 흑돼지 삼겹살 500g", "pork-belly,meat", 126),
    27: ProductImageSpec("목살 구이용 500g", "pork,meat", 127),
    28: ProductImageSpec("돼지갈비 양념 1kg", "pork-ribs,meat", 128),
    29: ProductImageSpec("노르웨이 생연어 300g", "salmon,seafood", 129),
    30: ProductImageSpec("제주 광어회 500g", "sashimi,fish", 130),
    31: ProductImageSpec("고등어 2마리", "mackerel,fish", 131),
    32: ProductImageSpec("참치회 400g", "tuna,sashimi", 132),
    33: ProductImageSpec("갈치 2마리", "cooked-fish", 2),
    34: ProductImageSpec("통영 생굴 1kg", "oyster,seafood", 134),
    35: ProductImageSpec("킹크랩 1마리 (1.5kg)", "king-crab,seafood", 135),
    36: ProductImageSpec("새우 (대) 1kg", "shrimp,seafood", 136),
    37: ProductImageSpec("전복 10마리", "abalone,seafood", 137),
    38: ProductImageSpec("오징어 3마리", "squid,seafood", 138),
    39: ProductImageSpec("유기농 블루베리 500g", "blueberry,fruit", 139),
    40: ProductImageSpec("친환경 방울토마토 1kg", "cherry-tomato", 1),
    41: ProductImageSpec("유기농 브로콜리 2개", "broccoli,vegetable", 141),
    42: ProductImageSpec("흙당근 2kg", "carrot,soil,vegetable", 142),
}

IMAGE_BASE_URL = "https://loremflickr.com"
STORE_IMAGE_SPECS = {
    1: ("orchard,fruit,farm", 901),
    2: ("vegetable,farm", 902),
    3: ("cattle,farm", 903),
    4: ("fish-market,seafood", 904),
    5: ("organic-farm,produce", 905),
}


def _image_url(spec: ProductImageSpec, size: int, variant: int = 0) -> str:
    return f"{IMAGE_BASE_URL}/{size}/{size}/{spec.keywords}?lock={spec.lock + variant * 100}"


def _store_image_url(store_id: int) -> str:
    keywords, lock = STORE_IMAGE_SPECS[store_id]
    return f"{IMAGE_BASE_URL}/200/200/{keywords}?lock={lock}"


def _review_image_url(product_id: int, review_id: int) -> str:
    spec = PRODUCT_IMAGE_SPECS.get(product_id)
    if spec is None:
        return f"{IMAGE_BASE_URL}/300/300/farm,produce?lock={1000 + review_id}"
    return _image_url(spec, 300, review_id + 10)


def _build_images(product_id: int) -> tuple[str, str]:
    spec = PRODUCT_IMAGE_SPECS[product_id]
    thumbnail = _image_url(spec, 400)
    gallery = [_image_url(spec, 600, idx) for idx in range(3)]
    return thumbnail, json.dumps(gallery, ensure_ascii=False)


def update_product_images() -> int:
    db = SessionLocal()
    updated = 0
    try:
        for store_id in STORE_IMAGE_SPECS:
            store = db.query(Store).filter(Store.id == store_id).first()
            if store is not None:
                store.image_url = _store_image_url(store_id)
                updated += 1

        for product_id, spec in PRODUCT_IMAGE_SPECS.items():
            product = db.query(Product).filter(Product.id == product_id).first()
            if product is None:
                print(f"SKIP #{product_id}: product not found ({spec.name})")
                continue
            if product.name != spec.name:
                print(
                    f"WARN #{product_id}: expected {spec.name!r}, "
                    f"found {product.name!r}; updating by id"
                )

            thumbnail, images = _build_images(product_id)
            product.thumbnail = thumbnail
            product.images = images
            updated += 1

        reviews = db.query(Review).filter(Review.images.isnot(None)).all()
        for review in reviews:
            review.images = json.dumps(
                [_review_image_url(review.product_id, review.id)],
                ensure_ascii=False,
            )
            updated += 1

        db.commit()
        return updated
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    count = update_product_images()
    print(f"Updated image URLs: {count}")
